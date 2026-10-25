"""
DOCINT — Orchestrator service (Phase 13, built around the product's actual
two-page UX direction)

Two entry points, one per frontend page:

  - run_single_pipeline_job(job_id, pipeline_name): the OCR page — pick a
    department + template + ONE pipeline; only that pipeline processes the
    document. A straightforward call into the Phase 4/6 registry.

  - run_pipeline_comparison_job(job_id, pipeline_names): the Orchestration
    page — pick a department + template + several pipelines together; every
    one of them runs on the SAME rendered pages (concurrently, via
    ThreadPoolExecutor — this is Phase 12's pipeline-level parallelism
    actually happening), and their results are reconciled field-by-field by
    app/ai/consensus.py.

Both are new, additive entry points on top of the pipeline registry. They do
NOT touch process_document_job or the DIRECT_LLM_CALL/OCR_STRATEGY-gated
path — that stays exactly as-is as DOCINT's existing production flow for
uploads that don't go through either new page. Nothing here changes it.
"""
from __future__ import annotations

import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import SessionLocal
from app.db.models import (
    Document,
    ProcessingJob,
    Template,
    ProcessingResult,
    PipelineRun,
    FieldExtraction,
    DocumentPage,
    Asset,
)
from app.pipelines.registry import get_pipeline
import app.pipelines  # noqa: F401 - ensure all pipelines are registered
from app.ai.field_fallback import classify_all_fields
from app.ai.consensus import compare_pipeline_results, _normalize, overall_consensus_rate
from app.fast.validate import assess
from app.ocr.image_extractor import extract_pages_as_images_with_metadata
from app.services.asset_service import get_asset_service
from app.services.extractor import (
    _record_event,
    _mark_failed,
    mark_job_permanently_failed,  # noqa: F401 - re-exported for app/workers/tasks.py's on_failure callbacks
    _is_transient_error,
    _rq_retries_remaining,
)

logger = get_logger(__name__)

# Pipelines whose tables are taken for a comparison job's result, in order of
# preference (see _pick_table_source). fast_ocr first: its table reader is the
# one tuned on this project's documents and works from page geometry.
TABLE_PIPELINE_PREFERENCE = ("fast_ocr", "surya", "learned_anchor", "tesseract")


def _db_row(row: dict) -> dict:
    """A row dict as FieldExtraction columns: the "_"-prefixed keys are helpers
    for the result envelope (page number, label, source), not columns."""
    return {k: v for k, v in row.items() if not k.startswith("_")}


def _envelope_field(r: dict) -> dict:
    """One field row as the result envelope's per-field entry."""
    p_num = r.get("_page_number") or 1
    return {
        "value": r["field_value"],
        "confidence": r["confidence"],
        "found": bool(r["field_value"]),
        "match_strategy": r.get("extraction_method"),
        "source_page": p_num,
        "page_number": p_num,
        "page_id": r.get("page_id"),
        "bbox": r.get("bbox_json"),
        "label": r.get("_label") or "",
        "source": r.get("_source") or "",
    }


def _tables_out(tables) -> list[dict]:
    """PipelineResult tables as the result envelope's table entries."""
    return [
        {
            "table_name": f"Table_{t.table_index}",
            "table_index": t.table_index,
            "page_number": t.page_number,
            "headers": t.headers,
            "rows": t.rows,
            "cells": getattr(t, "cells", []),
            "bbox": getattr(t, "bbox", None),
            "markdown": getattr(t, "markdown", ""),
        }
        for t in tables
    ]


def _passes_field_checks(fdef: dict | None, value) -> bool:
    """Does a value satisfy its template field's own checks (non-empty, type, pattern, length)? Same checks the
    extractors use (app/fast/validate.assess), minus the confidence threshold: this asks whether the value is
    plausible for the field, not whether the reader was sure."""
    try:
        return assess(fdef, str(value or ""), 1.0).status == "validated"
    except Exception:
        return True


def _fail_open_pipeline_runs(job_id: str, message: str) -> None:
    """Close every still-RUNNING PipelineRun of a job as FAILED. Called when a
    job dies: the failure path rolls the session back, which discards any
    status the orchestrator had set, and without this the runs (and the UI's
    per-pipeline lanes) stay RUNNING forever. Best-effort, own session."""
    try:
        with SessionLocal() as s:
            runs = s.query(PipelineRun).filter(PipelineRun.job_id == job_id, PipelineRun.status == "RUNNING").all()
            for run in runs:
                run.status = "FAILED"
                run.completed_at = datetime.datetime.now(datetime.timezone.utc)
                run.error_message = (run.error_message or message)[:2000]
            s.commit()
    except Exception:
        logger.error("orchestrator_close_pipeline_runs_failed", job_id=job_id, exc_info=True)


def build_field_extraction_rows_from_pipeline_result(
    db,
    job_id: str,
    pipeline_run_id: str,
    pipeline_result,
    *,
    confidence_threshold: float | None = None,
    is_final: int = 0,
    page_ids: dict[int, str] | None = None,
) -> list[dict]:
    """Pure function: turn one PipelineResult's fields into FIELD_EXTRACTIONS
    row dicts. Sibling of app/services/extractor.py::build_field_extraction_rows,
    which does the same job for the legacy ExtractionResult shape — this one
    works off the Phase 4 pipeline interface's FieldResult dataclass instead.

    Applies the same Phase 9 field-level needs_review flag as the legacy
    path, so a single-pipeline OCR-page run gets identical review semantics
    to today's production flow.
    """
    threshold = confidence_threshold if confidence_threshold is not None else settings.CONFIDENCE_THRESHOLD
    conf_by_field = {f.field_name: f.confidence for f in pipeline_result.fields}
    statuses = {
        s.field_name: s
        for s in classify_all_fields(conf_by_field, threshold)
    }
    asset_service = get_asset_service()
    rows = []
    for f in pipeline_result.fields:
        status = statuses.get(f.field_name)
        bbox_json = f.bbox or {}
        if isinstance(bbox_json, dict):
            bbox_json["page_number"] = f.page_number or 1
        
        if f.crop_image:
            try:
                page_n = f.page_number or 1
                crop_path = asset_service.save_crop(job_id, f.field_name, page_n, f.crop_image)
                asset = Asset(asset_type="crop_image", file_path=crop_path, mime_type="image/png")
                db.add(asset)
                db.flush()
                bbox_json["crop_asset_id"] = asset.id
            except Exception:
                logger.error("orchestrator_crop_asset_save_failed", job_id=job_id, field_name=f.field_name, exc_info=True)

        rows.append({
            "job_id": job_id,
            "pipeline_run_id": pipeline_run_id,
            "field_name": f.field_name,
            "field_value": f.value,
            "confidence": f.confidence,
            "extraction_method": f.extraction_method or pipeline_result.pipeline_name,
            "fallback_method": "needs_review" if status and status.needs_review else None,
            "bbox_json": bbox_json,
            "page_id": (page_ids or {}).get(f.page_number),
            "_page_number": f.page_number or 1,
            "_label": f.label or "",
            "_source": f.source or "",
            "is_final": is_final,
        })
    return rows


def _load_job_context(db, job_id: str):
    """Shared setup: fetch job/document/template, mark PROCESSING, render
    pages. Returns (job, doc, template, images, native_texts) or raises
    ValueError for a deterministic (non-retryable) setup problem."""
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found.")

    job.status = "PROCESSING"
    job.started_at = datetime.datetime.now(datetime.timezone.utc)
    doc = db.get(Document, job.document_id)
    if doc:
        doc.status = "PROCESSING"
    db.commit()

    template = db.get(Template, job.template_id)
    if not template:
        raise ValueError(f"Template {job.template_id} not found.")

    max_pages = settings.MAX_PAGES_TO_PROCESS
    images, _, native_texts = extract_pages_as_images_with_metadata(
        doc.file_path,
        doc.file_type,
        scanned_text_threshold=settings.SCANNED_PAGE_TEXT_THRESHOLD,
    )
    if max_pages > 0:
        images = images[:max_pages]
        native_texts = native_texts[:max_pages]
    if not images:
        raise ValueError("No pages could be extracted from the document.")

    # Phase 2/3/16: persist one DOCUMENT_PAGES row + a page-image evidence
    # asset per rendered page, same pattern as the legacy app/services/extractor.py
    # production path, so the OCR/Orchestration pages get the same provenance
    # records (and the Phase 16 evidence endpoint has something to serve).
    # Best-effort per page: a storage hiccup must not fail the whole document.
    asset_service = get_asset_service()
    page_ids: dict[int, str] = {}
    for i, img in enumerate(images):
        dp = DocumentPage(document_id=doc.id, page_number=i + 1, width=img.width, height=img.height)
        try:
            page_path = asset_service.save_page_image(doc.id, i + 1, img)
            asset = Asset(asset_type="page_image", file_path=page_path, mime_type="image/png")
            db.add(asset)
            db.flush()
            dp.image_asset_id = asset.id
            asset_service.save_thumbnail(doc.id, i + 1, img)
        except Exception:
            logger.error("orchestrator_page_asset_save_failed", document_id=doc.id, page=i + 1, exc_info=True)
        db.add(dp)
        db.flush()
        page_ids[i + 1] = dp.id
    _record_event(db, job.id, "PAGE_RENDERED", status="COMPLETED", metadata={"pages": len(images)})

    return job, doc, template, images, native_texts, page_ids


def _template_dict(template, native_texts: list[str], force_relearn: bool = False) -> dict:
    schema = template.template_json or {}
    return {
        "fields": schema.get("fields", []),
        "schema": schema,
        "native_texts": native_texts,
        "department": template.department.slug if template.department else "GENERAL",
        "doc_type": template.code,
        "template_version": template.version,
        "extraction_instructions": template.extraction_instructions,
        "template_id": template.id,
        # Consumed only by pipelines that declare it (currently learned_anchor):
        # user explicitly flagged this upload as a new/changed format, so the
        # pipeline should skip its stored geometry and relearn from scratch.
        "force_relearn": force_relearn,
    }


def run_single_pipeline_job(job_id: str, pipeline_name: str, force_relearn: bool = False) -> None:
    """OCR page: run exactly one chosen pipeline and persist its result as
    the job's final (is_final=1) field values — same persistence shape as
    today's production flow, just dispatched through the registry instead
    of the DIRECT_LLM_CALL/OCR_STRATEGY branch."""
    logger.info("orchestrator_single_pipeline_started", job_id=job_id, pipeline=pipeline_name)
    t_total = time.perf_counter()

    with SessionLocal() as db:
        try:
            job, doc, template, images, native_texts, page_ids = _load_job_context(db, job_id)
            pipeline = get_pipeline(pipeline_name)

            pipeline_run = PipelineRun(
                job_id=job.id,
                pipeline_name=pipeline_name,
                status="RUNNING",
                started_at=datetime.datetime.now(datetime.timezone.utc),
            )
            db.add(pipeline_run)
            db.flush()
            _record_event(db, job.id, "PIPELINE_STARTED", pipeline_run_id=pipeline_run.id, status="RUNNING")
            db.commit()

            t_last_event = time.perf_counter()

            def _single_event_callback(step_name: str, data: dict = None):
                nonlocal t_last_event
                now = time.perf_counter()
                dur_ms = int((now - t_last_event) * 1000)
                t_last_event = now
                with SessionLocal() as _db:
                    _record_event(_db, job.id, step_name, pipeline_run_id=pipeline_run.id, status="RUNNING", duration_ms=dur_ms, metadata=data)
                    _db.commit()

            result = pipeline.run(images, _template_dict(template, native_texts, force_relearn=force_relearn), job.id, event_callback=_single_event_callback)

            pipeline_run.status = "COMPLETED" if result.status == "completed" else result.status.upper()
            pipeline_run.completed_at = datetime.datetime.now(datetime.timezone.utc)
            pipeline_run.overall_confidence = result.overall_confidence
            pipeline_run.error_message = result.error_message
            pipeline_run.metadata_json = result.metadata
            _record_event(
                db, job.id, "PIPELINE_COMPLETED", pipeline_run_id=pipeline_run.id,
                status=pipeline_run.status,
            )

            if result.status == "failed":
                raise RuntimeError(result.error_message or f"Pipeline '{pipeline_name}' failed with no error message.")

            # Clean previous final field extractions for this job on re-run
            db.query(FieldExtraction).filter_by(job_id=job.id, is_final=1).delete()

            rows = build_field_extraction_rows_from_pipeline_result(
                db, job.id, pipeline_run.id, result, is_final=1, page_ids=page_ids,
            )
            for row in rows:
                db.add(FieldExtraction(**_db_row(row)))

            flagged = [r["field_name"] for r in rows if r["fallback_method"] == "needs_review"]
            if flagged:
                _record_event(
                    db, job.id, "FIELD_NEEDS_REVIEW", pipeline_run_id=pipeline_run.id,
                    status="WARNING", message=f"{len(flagged)} field(s) below confidence threshold",
                    metadata={"fields": flagged, "threshold": settings.CONFIDENCE_THRESHOLD},
                )

            # Build the ResponseBuilder-compatible envelope for the frontend
            fields_out = {r["field_name"]: _envelope_field(r) for r in rows}
            tables_out = _tables_out(result.tables)

            envelope = {
                "success": result.status == "completed",
                "department": template.department.slug if template.department else "GENERAL",
                "doc_type": template.code,
                "template_version": template.version,
                "extraction_method": pipeline_name,
                "ocr_method": pipeline_name,
                "confidence": round(result.overall_confidence, 4),
                "pages_processed": len(page_ids),
                "fields": fields_out,
                "tables": tables_out,
            }

            ocr_metadata = dict(result.metadata.get("ocr_metadata", {}))
            template_la = (template.template_json or {}).get("learned_anchors")
            final_la = result.metadata.get("learned_anchors") or template_la

            all_dets = result.all_detections or result.metadata.get("all_detections", [])

            ocr_metadata.update({
                "mode": result.metadata.get("mode", "single_pipeline"),
                "pipeline": pipeline_name,
                "speed": result.metadata.get("speed"),
                "anchor_matched": bool(result.metadata.get("anchor_matched") or template_la),
                "anchor_learned": result.metadata.get("anchor_learned"),
                "learned_anchors": final_la,
                "all_detections": all_dets,
                "doc_page_width": result.metadata.get("doc_page_width"),
                "doc_page_height": result.metadata.get("doc_page_height"),
            })

            existing_res = db.query(ProcessingResult).filter_by(job_id=job.id).first()
            if existing_res:
                existing_res.extracted_json = envelope
                existing_res.confidence_json = [{"field": r["field_name"], "confidence": r["confidence"]} for r in rows]
                existing_res.ocr_metadata_json = ocr_metadata
            else:
                db.add(ProcessingResult(
                    job_id=job.id,
                    extracted_json=envelope,
                    confidence_json=[{"field": r["field_name"], "confidence": r["confidence"]} for r in rows],
                    ocr_metadata_json=ocr_metadata,
                ))

            job_status = "WARNING" if flagged else "COMPLETED"
            job.status = job_status
            job.completed_at = datetime.datetime.now(datetime.timezone.utc)
            job.overall_confidence = result.overall_confidence
            if doc:
                doc.status = "COMPLETED" if job_status in ("COMPLETED", "WARNING") else job_status
            _record_event(db, job.id, "DOCUMENT_COMPLETED", pipeline_run_id=pipeline_run.id, status=job_status)
            db.commit()

            logger.info(
                "orchestrator_single_pipeline_success", job_id=job_id, pipeline=pipeline_name,
                total_ms=int((time.perf_counter() - t_total) * 1000),
            )

        except Exception as exc:
            db.rollback()
            if _is_transient_error(exc) and _rq_retries_remaining():
                logger.warning("orchestrator_single_pipeline_transient_failure_will_retry", job_id=job_id, error=str(exc))
                _fail_open_pipeline_runs(job_id, f"interrupted, job will be retried: {exc}")
                raise
            logger.error("orchestrator_single_pipeline_failed", job_id=job_id, pipeline=pipeline_name, error=str(exc))
            _fail_open_pipeline_runs(job_id, str(exc))
            _mark_failed(job_id, str(exc))


def _pick_table_source(raw_results: dict) -> str | None:
    """Which pipeline's tables go into the consensus envelope.

    Tables cannot be voted cell-by-cell the way scalar fields can, so the
    job's table output comes from ONE pipeline: the first (by
    TABLE_PIPELINE_PREFERENCE) that produced tables and did not fail; any
    pipeline outside that list is a last resort, ordered by row count. Every
    pipeline's own tables stay available in ocr_metadata_json.raw_results."""
    candidates = {
        name: res for name, (_pr_id, res) in raw_results.items()
        if res.status != "failed" and res.tables
    }
    if not candidates:
        return None
    for name in TABLE_PIPELINE_PREFERENCE:
        if name in candidates:
            return name
    return max(candidates, key=lambda n: sum(len(t.rows or []) for t in candidates[n].tables))


def run_pipeline_comparison_job(job_id: str, pipeline_names: list[str]) -> None:
    """Orchestration page: run every selected pipeline on the same rendered
    pages, concurrently, and reconcile their field values via
    app/ai/consensus.py. Persists each pipeline's raw results (is_final=0)
    plus one consensus row per field (is_final=1, extraction_method="consensus"),
    under a synthetic "consensus" PipelineRun so the final values are
    queryable without re-deriving them from the raw per-pipeline rows.

    The stored ProcessingResult.extracted_json uses the SAME envelope as a
    single-pipeline job (fields{} + tables[]), so review, edit, export and the
    status endpoints work on a comparison job exactly as on a single one.

    Consensus is voted on TEMPLATE fields only. Fields a pipeline merely
    discovered on the page (source == "discovered") are not part of the vote:
    one pipeline's extra find would otherwise count as a one-voter "consensus"
    and distort the rate. They are merged in afterwards (highest confidence per
    name) so nothing found is lost. If some pipelines failed, or fewer than two
    contributed, the job finishes as WARNING (never silently COMPLETED) and every
    field is flagged for review, because nothing was actually cross-checked."""
    logger.info("orchestrator_comparison_started", job_id=job_id, pipelines=pipeline_names)
    t_total = time.perf_counter()

    with SessionLocal() as db:
        try:
            job, doc, template, images, native_texts, page_ids = _load_job_context(db, job_id)
            template_dict = _template_dict(template, native_texts)
            # Comparison runs must never rewrite the template: a learned_anchor
            # run would otherwise persist anchors from a run nobody reviewed.
            template_dict["read_only"] = True

            pipelines = {name: get_pipeline(name) for name in pipeline_names}
            field_defs = {f.get("name"): f for f in template_dict["fields"] if isinstance(f, dict)}

            results_by_pipeline: dict[str, dict] = {}  # name -> {field_name: (value, confidence, bbox, page_id)}
            pipeline_statuses: dict[str, str] = {}
            pipeline_errors: dict[str, str] = {}

            pr_ids = {}
            for name in pipeline_names:
                pipeline_run = PipelineRun(
                    job_id=job.id,
                    pipeline_name=name,
                    status="RUNNING",
                    started_at=datetime.datetime.now(datetime.timezone.utc),
                )
                db.add(pipeline_run)
                db.flush()
                pr_ids[name] = pipeline_run.id
                _record_event(db, job.id, "PIPELINE_STARTED", pipeline_run_id=pipeline_run.id, status="RUNNING")
            db.commit()

            def _run_one(name: str, pipeline, pr_id: str):
                t_comp_last = time.perf_counter()
                def _comp_event_callback(step_name: str, data: dict = None):
                    nonlocal t_comp_last
                    now = time.perf_counter()
                    dur_ms = int((now - t_comp_last) * 1000)
                    t_comp_last = now
                    with SessionLocal() as _db:
                        _record_event(_db, job.id, step_name, pipeline_run_id=pr_id, status="RUNNING", duration_ms=dur_ms, metadata=data)
                        _db.commit()

                # IMPORTANT: Only the actual pipeline inference happens in the thread.
                # DB writes within the thread use their own short-lived SessionLocal.
                return name, pr_id, pipeline.run(images, template_dict, job.id, event_callback=_comp_event_callback)

            # Phase 12: these pipelines genuinely run concurrently now
            max_workers = max(1, min(len(pipelines), settings.OCR_MAX_WORKERS))
            raw_results: dict[str, tuple] = {}
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_run_one, name, p, pr_ids[name]): name for name, p in pipelines.items()}
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        pname, pr_id, result = future.result()
                        raw_results[pname] = (pr_id, result)
                    except Exception as pipeline_exc:
                        logger.error("orchestrator_pipeline_run_errored", job_id=job_id, pipeline=name, error=str(pipeline_exc))
                        pipeline_errors[name] = str(pipeline_exc) or type(pipeline_exc).__name__

            # A pipeline that raised never produced a result: close its run as
            # FAILED (with the reason) instead of leaving it RUNNING forever.
            for name, message in pipeline_errors.items():
                pipeline_statuses[name] = "FAILED"
                results_by_pipeline[name] = {}
                failed_run = db.get(PipelineRun, pr_ids[name])
                if failed_run:
                    failed_run.status = "FAILED"
                    failed_run.completed_at = datetime.datetime.now(datetime.timezone.utc)
                    failed_run.error_message = message[:2000]
                _record_event(db, job.id, "PIPELINE_FAILED", pipeline_run_id=pr_ids[name], status="FAILED", message=message[:2000])

            field_rows_by_pipeline: dict[str, dict[str, dict]] = {}
            discovered_rows: dict[str, dict] = {}  # field name -> best discovered row across pipelines

            # All DB writes happen here sequentially.
            for name, (pr_id, result) in raw_results.items():
                pipeline_run = db.get(PipelineRun, pr_id)
                if not pipeline_run:
                    continue
                pipeline_run.status = "COMPLETED" if result.status == "completed" else result.status.upper()
                pipeline_run.completed_at = datetime.datetime.now(datetime.timezone.utc)
                pipeline_run.overall_confidence = result.overall_confidence
                pipeline_run.error_message = result.error_message
                pipeline_run.metadata_json = result.metadata

                _record_event(db, job.id, "PIPELINE_COMPLETED", pipeline_run_id=pr_id, status=pipeline_run.status)
                pipeline_statuses[name] = pipeline_run.status
                if pipeline_run.status == "FAILED":
                    pipeline_errors[name] = result.error_message or "pipeline failed with no error message"

                rows = build_field_extraction_rows_from_pipeline_result(
                    db, job.id, pr_id, result, is_final=0, page_ids=page_ids,
                )
                for row in rows:
                    # "_"-prefixed keys are envelope-only helpers, not columns.
                    db.add(FieldExtraction(**_db_row(row)))

                template_rows = [r for r in rows if r.get("_source") != "discovered"]
                for r in rows:
                    if r.get("_source") == "discovered":
                        prev = discovered_rows.get(r["field_name"])
                        if prev is None or (r["confidence"] or 0.0) > (prev["confidence"] or 0.0):
                            discovered_rows[r["field_name"]] = r

                field_rows_by_pipeline[name] = {r["field_name"]: r for r in template_rows}
                results_by_pipeline[name] = {
                    r["field_name"]: (
                        r["field_value"], r["confidence"] or 0.0, r["bbox_json"], r["page_id"],
                        _passes_field_checks(field_defs.get(r["field_name"]), r["field_value"]),
                    ) for r in template_rows
                }

            db.flush()

            if not raw_results or not any(res.fields for _pr, res in raw_results.values()):
                detail = "; ".join(f"{n}: {m}" for n, m in pipeline_errors.items())
                raise RuntimeError(
                    f"All {len(pipeline_names)} selected pipelines failed to produce any fields."
                    + (f" ({detail})" if detail else "")
                )

            comparisons = compare_pipeline_results(results_by_pipeline)
            contributing = [n for n, fields in results_by_pipeline.items() if fields]
            cross_checked = len(contributing) >= 2
            if not cross_checked:
                # With a single contributing pipeline nothing was compared:
                # "single_pipeline" agreement must not read as consensus.
                for c in comparisons:
                    c.needs_review = True
            consensus_rate = overall_consensus_rate(comparisons)
            failed_pipelines = sorted(n for n, s in pipeline_statuses.items() if s == "FAILED")
            degraded = bool(failed_pipelines) or not cross_checked

            consensus_run = PipelineRun(
                job_id=job.id, pipeline_name="consensus", status="COMPLETED",
                started_at=datetime.datetime.now(datetime.timezone.utc),
                completed_at=datetime.datetime.now(datetime.timezone.utc),
                overall_confidence=(sum(c.consensus_confidence for c in comparisons) / len(comparisons)) if comparisons else 0.0,
                metadata_json={
                    "source_pipelines": list(pipelines.keys()),
                    "pipeline_statuses": pipeline_statuses,
                    "consensus_rate": consensus_rate,
                    "degraded": degraded,
                    "cross_checked": cross_checked,
                },
            )
            db.add(consensus_run)
            db.flush()

            flagged = []
            fields_out: dict[str, dict] = {}
            for c in comparisons:
                best_row = None
                for v in sorted(c.values, key=lambda x: x.confidence, reverse=True):
                    if _normalize(v.value) == _normalize(c.consensus_value):
                        best_row = field_rows_by_pipeline.get(v.pipeline_name, {}).get(c.field_name)
                        if best_row:
                            break

                db.add(FieldExtraction(
                    job_id=job.id,
                    pipeline_run_id=consensus_run.id,
                    field_name=c.field_name,
                    field_value=c.consensus_value,
                    confidence=c.consensus_confidence,
                    extraction_method="consensus",
                    fallback_method="needs_review" if c.needs_review else None,
                    is_final=1,
                    bbox_json=best_row["bbox_json"] if best_row else None,
                    page_id=best_row["page_id"] if best_row else None,
                ))
                entry = _envelope_field(best_row) if best_row else {
                    "page_number": 1, "source_page": 1, "page_id": None, "bbox": None, "label": "", "source": "",
                }
                entry.update({
                    "value": c.consensus_value,
                    "confidence": c.consensus_confidence,
                    "found": bool(c.consensus_value),
                    "match_strategy": "consensus",
                    "agreement": c.agreement,
                    "resolution_rule": c.resolution_rule,
                    "needs_review": c.needs_review,
                })
                fields_out[c.field_name] = entry
                if c.needs_review:
                    flagged.append(c.field_name)

            # Discovered (non-template) fields: outside the vote, kept in the result.
            for fname, r in discovered_rows.items():
                if fname in fields_out:
                    continue
                db.add(FieldExtraction(**{**_db_row(r), "pipeline_run_id": consensus_run.id, "is_final": 1}))
                fields_out[fname] = _envelope_field(r)

            table_source = _pick_table_source(raw_results)
            tables_out = _tables_out(raw_results[table_source][1].tables) if table_source else []

            if flagged:
                _record_event(
                    db, job.id, "FIELD_NEEDS_REVIEW", pipeline_run_id=consensus_run.id,
                    status="WARNING", message=f"{len(flagged)} field(s) have no pipeline majority",
                    metadata={"fields": flagged, "resolution_rule": "highest_confidence"},
                )
            if degraded:
                reason = (
                    "pipeline(s) failed: " + ", ".join(f"{n} ({pipeline_errors.get(n, 'unknown error')[:160]})" for n in failed_pipelines)
                    if failed_pipelines else "fewer than two pipelines produced fields"
                )
                _record_event(
                    db, job.id, "PIPELINE_DEGRADED", pipeline_run_id=consensus_run.id, status="WARNING",
                    message=f"Result is not cross-checked: {reason}",
                    metadata={"failed_pipelines": failed_pipelines, "contributing": contributing},
                )

            first_ok = next((res for _pr, res in raw_results.values() if res.status != "failed"), None)
            envelope = {
                "success": True,
                "department": template.department.slug if template.department else "GENERAL",
                "doc_type": template.code,
                "template_version": template.version,
                "extraction_method": "consensus",
                "ocr_method": "consensus",
                "confidence": round(consensus_run.overall_confidence or 0.0, 4),
                "pages_processed": len(page_ids),
                "fields": fields_out,
                "tables": tables_out,
                "pipelines": list(pipelines.keys()),
                "table_source_pipeline": table_source,
            }

            comparison_metadata = {
                "mode": "comparison",
                "pipelines": list(pipelines.keys()),
                "pipeline_statuses": pipeline_statuses,
                "pipeline_errors": pipeline_errors,
                "failed_pipelines": failed_pipelines,
                "degraded": degraded,
                "cross_checked": cross_checked,
                "consensus_rate": consensus_rate,
                "total_fields": len(comparisons),
                "flagged_fields": flagged,
                "table_source_pipeline": table_source,
                "doc_page_width": (first_ok.metadata.get("doc_page_width") if first_ok else None),
                "doc_page_height": (first_ok.metadata.get("doc_page_height") if first_ok else None),
                "raw_results": {
                    p_name: {
                        "tables": [
                            {
                                "table_index": t.table_index,
                                "page_number": t.page_number,
                                "headers": t.headers,
                                "rows": t.rows,
                                "confidence": t.confidence,
                                "bbox": t.bbox,
                                "extraction_method": t.extraction_method,
                            } for t in res.tables
                        ],
                        "raw_text_per_page": res.raw_text_per_page or [
                            "\n".join(line.get("text", "") for line in page.get("lines", []))
                            for page in res.metadata.get("ocr_metadata", {}).get("pages", [])
                        ],
                        "fields": { f.field_name: {"value": f.value, "confidence": f.confidence} for f in res.fields }
                    } for p_name, (pr_id, res) in raw_results.items()
                },
            }
            confidence_json = [
                {
                    "field": c.field_name,
                    "confidence": c.consensus_confidence,
                    "agreement": c.agreement,
                    "resolution_rule": c.resolution_rule,
                    "needs_review": c.needs_review,
                    "per_pipeline": {v.pipeline_name: {"value": v.value, "confidence": v.confidence} for v in c.values},
                }
                for c in comparisons
            ]

            existing_res = db.query(ProcessingResult).filter_by(job_id=job.id).first()
            if existing_res:
                existing_res.extracted_json = envelope
                existing_res.confidence_json = confidence_json
                existing_res.ocr_metadata_json = comparison_metadata
            else:
                db.add(ProcessingResult(
                    job_id=job.id,
                    extracted_json=envelope,
                    confidence_json=confidence_json,
                    ocr_metadata_json=comparison_metadata,
                ))

            job_status = "WARNING" if (flagged or degraded) else "COMPLETED"
            job.status = job_status
            job.completed_at = datetime.datetime.now(datetime.timezone.utc)
            job.overall_confidence = consensus_run.overall_confidence
            if doc:
                doc.status = "COMPLETED" if job_status in ("COMPLETED", "WARNING") else job_status
            _record_event(db, job.id, "DOCUMENT_COMPLETED", pipeline_run_id=consensus_run.id, status=job_status)
            db.commit()

            logger.info(
                "orchestrator_comparison_success", job_id=job_id, pipelines=pipeline_names,
                consensus_rate=consensus_rate, flagged=len(flagged), degraded=degraded,
                total_ms=int((time.perf_counter() - t_total) * 1000),
            )

        except Exception as exc:
            db.rollback()
            if _is_transient_error(exc) and _rq_retries_remaining():
                logger.warning("orchestrator_comparison_transient_failure_will_retry", job_id=job_id, error=str(exc))
                _fail_open_pipeline_runs(job_id, f"interrupted, job will be retried: {exc}")
                raise
            logger.error("orchestrator_comparison_failed", job_id=job_id, pipelines=pipeline_names, error=str(exc))
            _fail_open_pipeline_runs(job_id, str(exc))
            _mark_failed(job_id, str(exc))
