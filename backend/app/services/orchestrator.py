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
from app.ai.field_fallback import classify_all_fields
from app.ai.consensus import compare_pipeline_results, _normalize, overall_consensus_rate
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


def _template_dict(template, native_texts: list[str]) -> dict:
    schema = template.template_json or {}
    return {
        "fields": schema.get("fields", []),
        "schema": schema,
        "native_texts": native_texts,
        "department": template.department.slug if template.department else "GENERAL",
        "doc_type": template.code,
        "template_version": template.version,
        "extraction_instructions": template.extraction_instructions,
    }


def run_single_pipeline_job(job_id: str, pipeline_name: str) -> None:
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

            def _single_event_callback(step_name: str, data: dict = None):
                with SessionLocal() as _db:
                    _record_event(_db, job.id, step_name, pipeline_run_id=pipeline_run.id, status="RUNNING", metadata=data)
                    _db.commit()

            result = pipeline.run(images, _template_dict(template, native_texts), job.id, event_callback=_single_event_callback)

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

            rows = build_field_extraction_rows_from_pipeline_result(
                db, job.id, pipeline_run.id, result, is_final=1, page_ids=page_ids,
            )
            for row in rows:
                db.add(FieldExtraction(**row))

            flagged = [r["field_name"] for r in rows if r["fallback_method"] == "needs_review"]
            if flagged:
                _record_event(
                    db, job.id, "FIELD_NEEDS_REVIEW", pipeline_run_id=pipeline_run.id,
                    status="WARNING", message=f"{len(flagged)} field(s) below confidence threshold",
                    metadata={"fields": flagged, "threshold": settings.CONFIDENCE_THRESHOLD},
                )

            # Build the ResponseBuilder-compatible envelope for the frontend
            fields_out = {}
            for r in rows:
                fields_out[r["field_name"]] = {
                    "value": r["field_value"],
                    "confidence": r["confidence"],
                    "found": bool(r["field_value"]),
                    "match_strategy": r.get("extraction_method"),
                    "source_page": r.get("page_id"),
                }

            tables_out = []
            for t in result.tables:
                tables_out.append({
                    "table_name": f"Table_{t.table_index}",
                    "rows": t.rows
                })

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

            ocr_metadata = result.metadata.get("ocr_metadata", {})
            ocr_metadata.update({"mode": "single_pipeline", "pipeline": pipeline_name})

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
                doc.status = job_status
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
                raise
            logger.error("orchestrator_single_pipeline_failed", job_id=job_id, pipeline=pipeline_name, error=str(exc))
            _mark_failed(job_id, str(exc))


def run_pipeline_comparison_job(job_id: str, pipeline_names: list[str]) -> None:
    """Orchestration page: run every selected pipeline on the same rendered
    pages, concurrently, and reconcile their field values via
    app/ai/consensus.py. Persists each pipeline's raw results (is_final=0)
    plus one consensus row per field (is_final=1, extraction_method="consensus"),
    under a synthetic "consensus" PipelineRun so the final values are
    queryable without re-deriving them from the raw per-pipeline rows."""
    logger.info("orchestrator_comparison_started", job_id=job_id, pipelines=pipeline_names)
    t_total = time.perf_counter()

    with SessionLocal() as db:
        try:
            job, doc, template, images, native_texts, page_ids = _load_job_context(db, job_id)
            template_dict = _template_dict(template, native_texts)

            pipelines = {name: get_pipeline(name) for name in pipeline_names}

            results_by_pipeline: dict[str, dict] = {}  # name -> {field_name: (value, confidence)}
            pipeline_statuses: dict[str, str] = {}

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
                def _comp_event_callback(step_name: str, data: dict = None):
                    with SessionLocal() as _db:
                        _record_event(_db, job.id, step_name, pipeline_run_id=pr_id, status="RUNNING", metadata=data)
                        _db.commit()
                        
                # IMPORTANT: Only the actual pipeline inference happens in the thread.
                # DB writes within the thread use their own short-lived SessionLocal.
                return name, pr_id, pipeline.run(images, template_dict, job.id, event_callback=_comp_event_callback)

            # Phase 12: these pipelines genuinely run concurrently now
            max_workers = max(1, min(len(pipelines), settings.OCR_MAX_WORKERS))
            raw_results: dict[str, object] = {}
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_run_one, name, p, pr_ids[name]): name for name, p in pipelines.items()}
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        pname, pr_id, result = future.result()
                        raw_results[pname] = (pr_id, result)
                    except Exception as pipeline_exc:
                        logger.error("orchestrator_pipeline_run_errored", job_id=job_id, pipeline=name, error=str(pipeline_exc))
                        pipeline_statuses[name] = "FAILED"
                        results_by_pipeline[name] = {}

            field_rows_by_pipeline: dict[str, dict[str, dict]] = {}

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

                rows = build_field_extraction_rows_from_pipeline_result(
                    db, job.id, pr_id, result, is_final=0, page_ids=page_ids,
                )
                for row in rows:
                    db.add(FieldExtraction(**row))

                field_rows_by_pipeline[name] = {r["field_name"]: r for r in rows}
                results_by_pipeline[name] = {
                    r["field_name"]: (r["field_value"], r["confidence"] or 0.0, r["bbox_json"], r["page_id"]) for r in rows
                }

            db.flush()

            if not any(results_by_pipeline.values()):
                raise RuntimeError(f"All {len(pipeline_names)} selected pipelines failed to produce any fields.")

            comparisons = compare_pipeline_results(results_by_pipeline)
            consensus_rate = overall_consensus_rate(comparisons)

            consensus_run = PipelineRun(
                job_id=job.id, pipeline_name="consensus", status="COMPLETED",
                started_at=datetime.datetime.now(datetime.timezone.utc),
                completed_at=datetime.datetime.now(datetime.timezone.utc),
                overall_confidence=(sum(c.consensus_confidence for c in comparisons) / len(comparisons)) if comparisons else 0.0,
                metadata_json={
                    "source_pipelines": list(pipelines.keys()),
                    "pipeline_statuses": pipeline_statuses,
                    "consensus_rate": consensus_rate,
                },
            )
            db.add(consensus_run)
            db.flush()

            flagged = []
            for c in comparisons:
                best_row = None
                for v in c.values:
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
                if c.needs_review:
                    flagged.append(c.field_name)

            if flagged:
                _record_event(
                    db, job.id, "FIELD_NEEDS_REVIEW", pipeline_run_id=consensus_run.id,
                    status="WARNING", message=f"{len(flagged)} field(s) have no pipeline majority",
                    metadata={"fields": flagged, "resolution_rule": "highest_confidence"},
                )

            db.add(ProcessingResult(
                job_id=job.id,
                extracted_json={c.field_name: c.consensus_value for c in comparisons},
                confidence_json=[
                    {
                        "field": c.field_name,
                        "confidence": c.consensus_confidence,
                        "agreement": c.agreement,
                        "resolution_rule": c.resolution_rule,
                        "needs_review": c.needs_review,
                        "per_pipeline": {v.pipeline_name: {"value": v.value, "confidence": v.confidence} for v in c.values},
                    }
                    for c in comparisons
                ],
                ocr_metadata_json={
                    "mode": "comparison",
                    "pipelines": list(pipelines.keys()),
                    "pipeline_statuses": pipeline_statuses,
                    "consensus_rate": consensus_rate,
                    "total_fields": len(comparisons),
                    "flagged_fields": flagged,
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
                    }
                },
            ))

            job_status = "WARNING" if flagged else "COMPLETED"
            job.status = job_status
            job.completed_at = datetime.datetime.now(datetime.timezone.utc)
            job.overall_confidence = consensus_run.overall_confidence
            if doc:
                doc.status = job_status
            _record_event(db, job.id, "DOCUMENT_COMPLETED", pipeline_run_id=consensus_run.id, status=job_status)
            db.commit()

            logger.info(
                "orchestrator_comparison_success", job_id=job_id, pipelines=pipeline_names,
                consensus_rate=consensus_rate, flagged=len(flagged),
                total_ms=int((time.perf_counter() - t_total) * 1000),
            )

        except Exception as exc:
            db.rollback()
            if _is_transient_error(exc) and _rq_retries_remaining():
                logger.warning("orchestrator_comparison_transient_failure_will_retry", job_id=job_id, error=str(exc))
                raise
            logger.error("orchestrator_comparison_failed", job_id=job_id, pipelines=pipeline_names, error=str(exc))
            _mark_failed(job_id, str(exc))
