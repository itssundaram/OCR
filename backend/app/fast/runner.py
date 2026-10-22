"""
RQ entry point for one fast_ocr job: app.fast.runner.run_fast_job

Order of work (the point is that fields never wait for tables):

  1. read all pages (text layer or one cached OCR pass), announce fields live
  2. save the page images, final-match + validate the fields, VLM only for gaps
  3. COMMIT the fields (+ a first result row)         -> emit field_validated
  4. wait for the table jobs (each time-boxed)
  5. COMMIT tables + job status                        -> emit job_completed

`validated` is only emitted after the commit that saved the value.
"""
from __future__ import annotations

import datetime
import time
from concurrent.futures import ThreadPoolExecutor

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import SessionLocal
from app.db.models import (
    Asset, Document, DocumentPage, FieldExtraction, PipelineRun, ProcessingJob, ProcessingResult, Template,
)
from app.fast.engine import FastEngine, JobCancelled
from app.fast.events import EventBus
from app.fast.ocr_cache import default_cache
from app.fast.reader import PageReader
from app.fast.triage import DocumentSource
from app.services.asset_service import get_asset_service
from app.services.extractor import _mark_failed, _record_event
from app.services.orchestrator import _template_dict, build_field_extraction_rows_from_pipeline_result
from app.pipelines.base import PipelineResult

logger = get_logger(__name__)

PIPELINE_NAME = "fast_ocr"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _save_page_files(doc_id: str, page) -> str | None:
    """Worker-thread job: write the page PNG + thumbnail. Returns the PNG path."""
    svc = get_asset_service()
    try:
        path = svc.save_page_image(doc_id, page.page_number, page.image)
        svc.save_thumbnail(doc_id, page.page_number, page.image)
        return path
    except Exception:
        logger.error("fast_page_asset_save_failed", doc=doc_id, page=page.page_number, exc_info=True)
        return None


def run_fast_job(job_id: str, force_relearn: bool = False) -> None:
    t_total = time.perf_counter()
    bus: EventBus | None = None
    cache = default_cache()
    io_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fast-io")
    try:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            if not job:
                raise ValueError(f"Job {job_id} not found.")
            job.status = "PROCESSING"
            job.started_at = _now()
            doc = db.get(Document, job.document_id)
            if doc:
                doc.status = "PROCESSING"
            template = db.get(Template, job.template_id)
            if not template:
                raise ValueError(f"Template {job.template_id} not found.")
            db.commit()

            pipeline_run = PipelineRun(job_id=job.id, pipeline_name=PIPELINE_NAME, status="RUNNING", started_at=_now())
            db.add(pipeline_run)
            db.flush()
            _record_event(db, job.id, "PIPELINE_STARTED", pipeline_run_id=pipeline_run.id, status="RUNNING")
            db.commit()
            pr_id = pipeline_run.id

            t_last = time.perf_counter()

            def _coarse(step: str, data: dict | None = None) -> None:
                nonlocal t_last
                now = time.perf_counter()
                dur, t_last = int((now - t_last) * 1000), now
                with SessionLocal() as _db:
                    _record_event(_db, job_id, step, pipeline_run_id=pr_id, status="RUNNING", duration_ms=dur, metadata=data)
                    _db.commit()

            bus = EventBus(job_id, coarse_callback=_coarse)
            tdict = _template_dict(template, [], force_relearn=force_relearn)
            tfields = tdict["fields"]
            want_tables = any(str(f.get("type") or "").lower() == "table" for f in tfields)

            source = DocumentSource(doc.file_path, doc.file_type)
            reader = PageReader(source, cache)
            engine = FastEngine(tfields, bus, want_tables=want_tables)
            bus.emit("job_started", pages_total=source.page_count, template=template.code,
                     fields_total=len([f for f in tfields if str(f.get("type") or "").lower() != "table"]),
                     tables_expected=want_tables)

            saves: dict[int, object] = {}
            engine.run_pages(
                source.page_count, reader.read,
                on_page=lambda p: saves.__setitem__(p.page_number, io_pool.submit(_save_page_files, doc.id, p)),
            )

            # ── page rows + assets (needed for page_id on every field row) ──────
            page_ids: dict[int, str] = {}
            for page in engine.sorted_pages():
                dp = DocumentPage(document_id=doc.id, page_number=page.page_number, width=page.width, height=page.height)
                path = saves[page.page_number].result()
                if path:
                    asset = Asset(asset_type="page_image", file_path=path, mime_type="image/png")
                    db.add(asset)
                    db.flush()
                    dp.image_asset_id = asset.id
                db.add(dp)
                db.flush()
                page_ids[page.page_number] = dp.id
            _record_event(db, job.id, "PAGE_RENDERED", status="COMPLETED", metadata={"pages": len(page_ids)})

            # ── fields (final match, proximity, VLM repair, validation) ─────────
            fields = engine.finalize_fields()
            result = PipelineResult(
                pipeline_name=PIPELINE_NAME, status="completed", fields=fields,
                overall_confidence=engine.overall_confidence(fields),
            )
            rows = build_field_extraction_rows_from_pipeline_result(
                db, job.id, pr_id, result, is_final=1, page_ids=page_ids,
            )
            meta = engine.extractor.meta
            review_names = {n for n, m in meta.items() if m.state == "review_required"}
            db.query(FieldExtraction).filter_by(job_id=job.id, is_final=1).delete()
            for row in rows:
                if row["field_name"] in review_names:
                    row["fallback_method"] = "needs_review"
                db.add(FieldExtraction(**{k: v for k, v in row.items() if not k.startswith("_")}))

            fields_out = {}
            for r in rows:
                p_num = r.get("_page_number") or 1
                m = meta.get(r["field_name"])
                fields_out[r["field_name"]] = {
                    "value": r["field_value"], "confidence": r["confidence"], "found": bool(r["field_value"]),
                    "match_strategy": r.get("extraction_method"), "source_page": p_num, "page_number": p_num,
                    "page_id": r.get("page_id"), "bbox": r.get("bbox_json"),
                    "label": r.get("_label") or "", "source": r.get("_source") or "",
                    "state": m.state if m else "", "checks": m.checks if m else [], "reason": m.reason if m else "",
                }
            envelope = {
                "success": True,
                "department": template.department.slug if template.department else "GENERAL",
                "doc_type": template.code, "template_version": template.version,
                "extraction_method": PIPELINE_NAME, "ocr_method": PIPELINE_NAME,
                "confidence": result.overall_confidence, "pages_processed": len(page_ids),
                "fields": fields_out, "tables": [],
            }
            pages = engine.sorted_pages()
            ocr_metadata = {
                "mode": "fast_ocr", "pipeline": PIPELINE_NAME,
                "speed": engine.timings, "all_detections": engine.all_detections(),
                "doc_page_width": pages[0].width if pages else None,
                "doc_page_height": pages[0].height if pages else None,
                "page_sources": {str(p.page_number): p.source for p in pages},
                "blocks": engine.blocks, "warnings": engine.warnings,
                "anchor_matched": False, "anchor_learned": None, "learned_anchors": (template.template_json or {}).get("learned_anchors"),
            }
            conf_json = [{"field": r["field_name"], "confidence": r["confidence"]} for r in rows]
            res_row = db.query(ProcessingResult).filter_by(job_id=job.id).first()
            if res_row:
                res_row.extracted_json, res_row.confidence_json, res_row.ocr_metadata_json = envelope, conf_json, ocr_metadata
            else:
                res_row = ProcessingResult(job_id=job.id, extracted_json=envelope, confidence_json=conf_json, ocr_metadata_json=ocr_metadata)
                db.add(res_row)
            job.overall_confidence = result.overall_confidence
            db.commit()                                        # ← fields are now saved
            engine.extractor.emit_final_events(fields)

            # ── tables (time-boxed; never blocks the fields above) ───────────────
            tables = engine.collect_tables()
            tables_out = [{
                "table_name": f"Table_{t.table_index}", "table_index": t.table_index, "page_number": t.page_number,
                "headers": t.headers, "rows": t.rows, "cells": t.cells, "bbox": t.bbox, "markdown": t.markdown,
            } for t in tables]
            res_row.extracted_json = {**envelope, "tables": tables_out}
            ocr_metadata["speed"] = engine.timings
            ocr_metadata["warnings"] = engine.warnings
            res_row.ocr_metadata_json = dict(ocr_metadata)

            flagged = [n for n in review_names]
            status = "WARNING" if flagged else "COMPLETED"
            if flagged:
                _record_event(db, job.id, "FIELD_NEEDS_REVIEW", pipeline_run_id=pr_id, status="WARNING",
                              message=f"{len(flagged)} field(s) need review", metadata={"fields": flagged})
            pr = db.get(PipelineRun, pr_id)
            pr.status, pr.completed_at, pr.overall_confidence = "COMPLETED", _now(), result.overall_confidence
            pr.metadata_json = {"speed": engine.timings}
            job.status, job.completed_at = status, _now()
            if doc:
                doc.status = "COMPLETED"
            _record_event(db, job.id, "DOCUMENT_COMPLETED", pipeline_run_id=pr_id, status=status)
            db.commit()                                        # ← job is now complete

            total_ms = int((time.perf_counter() - t_total) * 1000)
            counts = {
                "fields": len([f for f in fields if f.source != "discovered"]),
                "validated": len([n for n, m in meta.items() if m.state == "validated"]),
                "review_required": len(review_names),
                "failed": len([n for n, m in meta.items() if m.state == "failed"]),
                "discovered": len([f for f in fields if f.source == "discovered"]),
                "tables": len(tables),
            }
            bus.emit("job_completed", status=status, counts=counts, timings_ms={**engine.timings, "total_ms": total_ms})
            logger.info("fast_job_done", job_id=job_id, total_ms=total_ms, **counts)
            cache.prune()
    except JobCancelled:
        logger.info("fast_job_cancelled", job_id=job_id)
        if bus:
            bus.emit("job_failed", error={"code": "cancelled", "message": "Cancelled by user", "retryable": False})
        _mark_failed(job_id, "Cancelled by user")
    except Exception as exc:
        logger.error("fast_job_failed", job_id=job_id, error=str(exc), exc_info=True)
        if bus:
            bus.emit("job_failed", error={"code": type(exc).__name__, "message": str(exc)[:500], "retryable": False})
        _mark_failed(job_id, str(exc))
    finally:
        io_pool.shutdown(wait=False)
