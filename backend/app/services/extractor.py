"""
DOCINT — Document Extraction Service
Processes a document end-to-end. Runs as a Redis RQ job (or BackgroundTask fallback).

Pipeline per job:
  1. PyMuPDF  — render all pages to PIL Images with adaptive DPI
  2. Parallel  — Tesseract OCR + img2table for all pages concurrently (ThreadPoolExecutor)
  3. LLM       — single Ollama call with combined OCR result + pre-rendered images
  4. Oracle    — save ProcessingResult

Page parallelism is controlled by settings.OCR_MAX_WORKERS.
img2table is skipped for native-text pages when settings.IMG2TABLE_SKIP_NATIVE=True.

Phase 4 note (implementation plan): the two branches below (DIRECT_LLM_CALL
True/False) now also exist as registered, working pipelines in app/pipelines/
(QwenPipeline, LegacyOCRPipeline) behind settings.USE_PIPELINE_REGISTRY — but
this function does not yet dispatch through them. That cutover is deliberately
staged separately so the live job flow here is untouched until the adapters
have been validated against real documents.
"""
from __future__ import annotations

import datetime
import time
import traceback
import io
import rq
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import SessionLocal
from app.db.models import (
    Document,
    ProcessingJob,
    Template,
    ProcessingResult,
    DocumentPage,
    PipelineRun,
    FieldExtraction,
    ProcessingEvent,
    Asset,
)
from app.services.asset_service import get_asset_service
from app.ocr.image_extractor import extract_pages_as_images_with_metadata
from app.ocr.tesseract import TesseractOCREngine
# NOTE: img2table_engine is imported lazily inside _process_single_page().
# This avoids loading numba at startup (which may be blocked by Application
# Control policies) when DIRECT_LLM_CALL=True skips the OCR path entirely.
from app.ai.orchestrator import ai_orchestrator
from app.ai.field_fallback import classify_all_fields, fields_needing_fallback
from app.core.concurrency import gpu_inference_slot
from app.ocr.base import OCRResult, OCRPage, OCRLine, OCRTable
from app.ocr.preprocessing import preprocess_page
from app.ocr.surya_layout_engine import SuryaLayoutEngine
from app.ocr.surya_ocr_engine import SuryaOCREngine
from app.ocr.handwriting_ocr import HandwritingOCREngine
from app.ocr.region_processor import process_region

logger = get_logger(__name__)

# Global instances for Surya to avoid reloading models per page/thread
_surya_layout_engine = None
_surya_ocr_engine = None
_handwriting_engine = None

def _get_surya_engines():
    global _surya_layout_engine, _surya_ocr_engine, _handwriting_engine
    
    if _surya_layout_engine is None and settings.SURYA_LAYOUT_ENABLED:
        _surya_layout_engine = SuryaLayoutEngine()
    if _surya_ocr_engine is None:
        _surya_ocr_engine = SuryaOCREngine()
    if _handwriting_engine is None and settings.HANDWRITING_FALLBACK_ENABLED:
        _handwriting_engine = HandwritingOCREngine()
        
    return _surya_layout_engine, _surya_ocr_engine, _handwriting_engine



_TRANSIENT_EXCEPTION_NAMES = {
    # SQLAlchemy / oracledb / redis wrap connectivity issues in driver-specific
    # exception classes; matching by name avoids importing every driver just
    # for isinstance checks.
    "OperationalError", "DisconnectionError", "DBAPIError", "InterfaceError",
    "ConnectionError", "TimeoutError", "RedisConnectionError", "RedisTimeoutError",
    "ConnectionResetError", "ConnectionRefusedError",
}


def _is_transient_error(exc: Exception) -> bool:
    """True for infra hiccups worth a bounded retry (DB/Redis connectivity,
    timeouts, filesystem/network blips) — False for deterministic errors (bad
    template, unreadable document, validation failures) that would fail
    identically on retry. Master prompt section 49: "do not retry
    indefinitely" — i.e. do not retry errors a retry can't fix.

    Pure function, no RQ/DB involved — see tests/unit/test_job_retry_policy.py.
    """
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return type(exc).__name__ in _TRANSIENT_EXCEPTION_NAMES


def _rq_retries_remaining() -> bool:
    """True only when this call is running inside an RQ worker AND that job's
    `retry=Retry(max=...)` policy (set at enqueue time in
    app/api/routes/extract.py) still has attempts left. False under the
    BackgroundTasks fallback (no RQ context at all) or once retries are
    exhausted — in both cases we must mark the job FAILED ourselves rather
    than re-raise into nothing.
    """
    try:
        current = rq.get_current_job()
    except Exception:
        return False
    if current is None:
        return False
    return bool(getattr(current, "retries_left", 0))


def _mark_failed(job_id: str, error_message: str) -> None:
    """Mark a job and its document as FAILED in the database."""
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        if job:
            job.status = "FAILED"
            job.error_message = error_message
            job.completed_at = datetime.datetime.now(datetime.timezone.utc)
            doc = db.get(Document, job.document_id)
            if doc:
                doc.status = "FAILED"
            try:
                db.add(ProcessingEvent(
                    job_id=job_id,
                    event_type="PIPELINE_FAILED",
                    status="FAILED",
                    message=error_message[:2000],
                ))
            except Exception:
                logger.error("event_record_failed", job_id=job_id, event_type="PIPELINE_FAILED", exc_info=True)
            db.commit()


def mark_job_permanently_failed(job_id: str, error_message: str) -> None:
    """Public entry point for the RQ on_failure callback (Phase 10,
    app/workers/tasks.py::job_failed_callback) — fires only once a job's
    bounded Retry policy is exhausted, or on an error that bypassed the
    try/except below entirely. Without this, such a job would leave its
    Oracle ProcessingJob row stuck at PROCESSING forever."""
    _mark_failed(job_id, error_message)


def _record_event(
    db,
    job_id: str,
    event_type: str,
    *,
    pipeline_run_id: str | None = None,
    status: str | None = None,
    message: str | None = None,
    duration_ms: int | None = None,
    metadata: dict | None = None,
) -> None:
    """Best-effort write to PROCESSING_EVENTS. Never raises — observability must not
    be able to fail document processing (Phase 2/14 of the implementation plan)."""
    try:
        db.add(ProcessingEvent(
            job_id=job_id,
            pipeline_run_id=pipeline_run_id,
            event_type=event_type,
            status=status,
            message=message,
            duration_ms=duration_ms,
            metadata_json=metadata,
        ))
    except Exception:
        logger.error("event_record_failed", job_id=job_id, event_type=event_type, exc_info=True)


def build_field_extraction_rows(
    job_id: str,
    pipeline_run_id: str,
    ai_result,
    *,
    confidence_threshold: float | None = None,
) -> list[dict]:
    """Pure function: turn one ExtractionResult into FIELD_EXTRACTIONS row dicts.

    Kept separate from process_document_job so it can be unit tested without a
    database (see tests/unit/test_extractor_persistence.py) — Phase 2/25 of the
    implementation plan.

    Phase 9: each row's `fallback_method` is set to "needs_review" when that
    field's confidence is below `confidence_threshold` (defaults to
    settings.CONFIDENCE_THRESHOLD) — a field-level flag, never a document-level
    one, per master prompt section 9. This only flags; it does not itself
    retry anything (see app/ai/field_fallback.py's module docstring for why).
    """
    threshold = confidence_threshold if confidence_threshold is not None else settings.CONFIDENCE_THRESHOLD
    conf_by_field = {c.field_name: c.confidence_score for c in ai_result.confidence_scores}
    statuses = {
        s.field_name: s
        for s in classify_all_fields(
            {name: conf_by_field.get(name) for name in (ai_result.parsed_data or {})},
            threshold,
        )
    }
    rows = []
    for field_name, field_value in (ai_result.parsed_data or {}).items():
        status = statuses.get(field_name)
        rows.append({
            "job_id": job_id,
            "pipeline_run_id": pipeline_run_id,
            "field_name": field_name,
            "field_value": str(field_value) if field_value is not None else None,
            "confidence": conf_by_field.get(field_name),
            "extraction_method": ai_result.engine_name,
            "fallback_method": "needs_review" if status and status.needs_review else None,
            "is_final": 1,
        })
    return rows


def _run_surya_on_page(
    img: Image.Image,
    *,
    use_tesseract_fallback: bool = False,
) -> tuple[list[OCRLine], list[OCRTable], float, str, int, int]:
    """Phase 6: Surya layout detection -> region-routed OCR -> handwriting/
    low-confidence fallback, extracted out of _process_single_page's
    if/else so it's independently callable. Pulled out verbatim — same
    engines, same region_processor call, same fallback-engine selection —
    so there is no new Surya code path to drift from the one that has run
    in production (settings.OCR_STRATEGY="surya" in .env) all along.

    `use_tesseract_fallback` mirrors what OCR_STRATEGY=="surya_with_fallback"
    already selected inline: Tesseract instead of TrOCR as the handwriting/
    low-confidence fallback engine.
    """
    img = preprocess_page(img)
    width, height = img.width, img.height

    layout_engine, ocr_engine, hw_engine = _get_surya_engines()

    if use_tesseract_fallback:
        from app.ocr.tesseract import TesseractOCREngine
        hw_engine = TesseractOCREngine()

    # Phase 12: bounds concurrent GPU-model calls across the ThreadPoolExecutor
    # threads that may all be inside this function at once (one per page, up
    # to OCR_MAX_WORKERS) — a no-op today (GPU_INFERENCE_CONCURRENCY=0), see
    # app/core/concurrency.py's module docstring for why no limit is guessed.
    with gpu_inference_slot():
        if layout_engine:
            regions = layout_engine.detect_layout(img)
        else:
            # Fallback if layout is disabled, treat whole page as text
            from app.ocr.surya_layout_engine import LayoutRegion
            from app.ocr.base import BoundingBox
            regions = [LayoutRegion("Text", BoundingBox(x1=0, y1=0, x2=width, y2=height), 1.0, img)]

        lines: list[OCRLine] = []
        extracted_tables: list[OCRTable] = []
        total_conf = 0.0

        for region in regions:
            res = process_region(region, ocr_engine, hw_engine, None)  # None for llm_engine for now
            lines.extend(res.lines)
            if res.table:
                extracted_tables.append(res.table)
            total_conf += res.confidence

    avg_conf = total_conf / len(regions) if regions else 0.0
    return lines, extracted_tables, avg_conf, "surya", width, height


def _process_single_page(
    page_index: int,
    img: Image.Image,
    native_text: str,
) -> tuple[int, list[OCRLine], list[OCRTable], bool, float, str, int, int]:
    """
    Process one page. Supports both Tesseract and Surya pipelines.
    """
    width, height = img.width, img.height

    # If the user selected strictly "surya" strategy, we skip the native text check
    # and force it to go through the Surya OCR pipeline.
    if native_text and settings.OCR_STRATEGY != "surya":
        # Native-text PDF page: PyMuPDF already extracted the text perfectly.
        lines: list[OCRLine] = [OCRLine(text=native_text, confidence=1.0)]
        is_native = True
        avg_conf = 1.0
        ocr_method = "native_text"
        
        # Always run img2table for tables
        from app.ocr.img2table_engine import extract_tables_from_image
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        extracted_tables = extract_tables_from_image(buf.getvalue())
        
        return page_index, lines, extracted_tables, is_native, avg_conf, ocr_method, width, height

    is_native = False

    if settings.OCR_STRATEGY in ("surya", "surya_with_fallback"):
        # Phase 6: the actual Surya layout+OCR+fallback logic now lives in
        # _run_surya_on_page, extracted out so it's independently callable —
        # by this dispatch (unchanged behavior) and by the standalone
        # SuryaPipeline (app/pipelines/surya_pipeline.py), which can select
        # Surya per template/department regardless of this global setting.
        lines, extracted_tables, avg_conf, ocr_method, width, height = _run_surya_on_page(
            img, use_tesseract_fallback=(settings.OCR_STRATEGY == "surya_with_fallback")
        )

    else:
        # ── TESSERACT PIPELINE ────────────────────────────────────────────────
        from app.ocr.img2table_engine import extract_tables_from_image
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        extracted_tables = extract_tables_from_image(buf.getvalue())
        
        engine = TesseractOCREngine()
        res = engine.process_images([img])
        if res.pages and res.pages[0].lines:
            lines = res.pages[0].lines
            avg_conf = sum(ln.confidence for ln in lines) / len(lines)
        else:
            lines = []
            avg_conf = 0.0
        ocr_method = "tesseract"

    return page_index, lines, extracted_tables, is_native, avg_conf, ocr_method, width, height


def process_document_job(job_id: str) -> None:
    """
    Main entry point called by the Redis RQ worker (or BackgroundTask fallback).
    Processes the document end-to-end and writes results to Oracle.
    """
    logger.info("extractor_started", job_id=job_id)
    t_total = time.perf_counter()

    max_pages = settings.MAX_PAGES_TO_PROCESS  # 0 = all pages

    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        if not job:
            logger.error("job_not_found", job_id=job_id)
            return

        job.status = "PROCESSING"
        job.started_at = datetime.datetime.now(datetime.timezone.utc)
        doc = db.get(Document, job.document_id)
        if doc:
            doc.status = "PROCESSING"
        db.commit()

        try:
            template = db.get(Template, job.template_id)
            if not template:
                raise ValueError(f"Template {job.template_id} not found.")

            schema = template.template_json

            # ── Step 1: Render all pages to PIL Images ────────────────────────
            t_render = time.perf_counter()
            # When DIRECT_LLM_CALL=True, Tesseract is skipped so we only need
            # enough resolution for the LLM's 800px thumbnail. Use a lower DPI
            # (OCR_DPI_DIRECT_LLM = 1.0x / ~72 DPI) to render much faster.
            # When using the old OCR path, keep the high DPI for Tesseract accuracy.
            if settings.DIRECT_LLM_CALL:
                render_dpi = settings.OCR_DPI_DIRECT_LLM
            else:
                render_dpi = None  # let extractor use OCR_DPI_NATIVE / OCR_DPI_SCANNED from settings

            images, _, native_texts = extract_pages_as_images_with_metadata(
                doc.file_path,
                doc.file_type,
                scanned_text_threshold=settings.SCANNED_PAGE_TEXT_THRESHOLD,
                dpi_native=render_dpi,
                dpi_scanned=render_dpi,
            )
            if max_pages > 0:
                images = images[:max_pages]
                native_texts = native_texts[:max_pages]

            if not images:
                raise ValueError("No pages could be extracted from the document.")
            render_ms = int((time.perf_counter() - t_render) * 1000)
            logger.info("pages_rendered", count=len(images), render_ms=render_ms, dpi_mode="direct_llm" if settings.DIRECT_LLM_CALL else "ocr")

            # ── Phase 2: one DOCUMENT_PAGES row per rendered page ─────────────
            # ── Phase 3: persist each page's image as an evidence asset ──────
            # Best-effort per page: a storage hiccup on one page must not fail
            # the whole document — it just means that page has no evidence yet.
            asset_service = get_asset_service()
            for i, img in enumerate(images):
                dp = DocumentPage(document_id=doc.id, page_number=i + 1, width=img.width, height=img.height)
                try:
                    page_path = asset_service.save_page_image(doc.id, i + 1, img)
                    asset = Asset(asset_type="page_image", file_path=page_path, mime_type="image/png")
                    db.add(asset)
                    db.flush()  # assign asset.id before linking it below
                    dp.image_asset_id = asset.id
                    asset_service.save_thumbnail(doc.id, i + 1, img)
                except Exception:
                    logger.error("page_asset_save_failed", document_id=doc.id, page=i + 1, exc_info=True)
                db.add(dp)
            db.flush()
            _record_event(db, job.id, "PAGE_RENDERED", status="COMPLETED", duration_ms=render_ms, metadata={"pages": len(images)})

            # ── Step 2: OCR + img2table (or skip via DIRECT_LLM_CALL) ────────
            max_workers = 0  # default for DIRECT_LLM_CALL path; overwritten in else branch
            if settings.DIRECT_LLM_CALL:
                # ── FAST PATH ─────────────────────────────────────────────────
                # Skip Tesseract OCR and img2table entirely.
                # Build a lightweight OCRResult stub — no text extraction,
                # just the page dimensions needed for the DB record.
                # The LLM call below uses page_images directly, so no text is needed.
                ocr_ms = 0
                ocr_method_overall = "direct_llm"
                pages: list[OCRPage] = [
                    OCRPage(
                        page_number=i + 1,
                        width=img.width,
                        height=img.height,
                        lines=[],
                        tables=[],
                        is_native_text=False,
                        extra_metadata={"avg_confidence": 0.0, "method": "direct_llm"},
                    )
                    for i, img in enumerate(images)
                ]
                ocr_result = OCRResult(
                    pages=pages,
                    engine_name="direct_llm",
                    engine_version="1.0",
                    ocr_method=ocr_method_overall,
                )
                logger.info(
                    "direct_llm_path_active",
                    pages=len(pages),
                    ocr_ms=ocr_ms,
                    note="Tesseract and img2table skipped",
                )

            else:
                # ── EXISTING PATH ──────────────────────────────────────────────
                # Each page is processed in a separate thread. Tesseract spawns
                # its own OS process so threads run truly concurrently (no GIL).
                t_ocr = time.perf_counter()
                max_workers = min(len(images), settings.OCR_MAX_WORKERS)
                page_results: dict[int, tuple] = {}

                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_idx = {
                        executor.submit(_process_single_page, i, img, native_texts[i]): i
                        for i, img in enumerate(images)
                    }
                    for future in as_completed(future_to_idx):
                        try:
                            result = future.result()
                            page_results[result[0]] = result[1:]  # drop index, keep rest
                        except Exception as page_err:
                            pg_idx = future_to_idx[future]
                            logger.error("page_ocr_failed", page=pg_idx + 1, error=str(page_err))
                            # Store empty result so we still build OCRPage for this page
                            page_results[pg_idx] = ([], [], False, 0.0, "failed", images[pg_idx].width, images[pg_idx].height)

                # Reassemble pages in original order
                pages: list[OCRPage] = []
                for i in sorted(page_results.keys()):
                    lines, tables, is_native, avg_conf, ocr_method, width, height = page_results[i]
                    pages.append(OCRPage(
                        page_number=i + 1,
                        width=width,
                        height=height,
                        lines=lines,
                        tables=tables,
                        is_native_text=is_native,
                        extra_metadata={"avg_confidence": avg_conf, "method": ocr_method},
                    ))

                ocr_ms = int((time.perf_counter() - t_ocr) * 1000)
                ocr_method_overall = "native_text" if all(p.is_native_text for p in pages) else settings.OCR_STRATEGY

                ocr_result = OCRResult(
                    pages=pages,
                    engine_name="parallel_pipeline",
                    engine_version="2.0",
                    ocr_method=ocr_method_overall,
                )
                logger.info(
                    "ocr_complete",
                    pages=len(pages),
                    workers=max_workers,
                    ocr_ms=ocr_ms,
                    method=ocr_method_overall,
                )

            # ── Phase 2: one PIPELINE_RUNS row per extraction attempt ─────────
            pipeline_run = PipelineRun(
                job_id=job.id,
                pipeline_name=ocr_method_overall,
                status="RUNNING",
                started_at=datetime.datetime.now(datetime.timezone.utc),
                metadata_json={"ocr_engine": ocr_result.engine_name, "ocr_engine_version": ocr_result.engine_version},
            )
            db.add(pipeline_run)
            db.flush()
            _record_event(db, job.id, "PIPELINE_STARTED", pipeline_run_id=pipeline_run.id, status="RUNNING")

            # ── Step 3: LLM Extraction ────────────────────────────────────────
            # Pass the already-rendered images directly — avoids a second disk read
            # inside the orchestrator. Images are resized to LLM_IMAGE_MAX_PX there.
            t_ai = time.perf_counter()
            ai_result = ai_orchestrator.run_smart_extraction(
                ocr_result,
                schema,
                doc_id=str(doc.id),
                department=template.department.slug if template.department else "GENERAL",
                doc_type=template.code,
                template_version=template.version,
                extraction_instructions=template.extraction_instructions,
                page_images=images,  # pre-rendered, no second disk read
            )
            ai_ms = int((time.perf_counter() - t_ai) * 1000)

            # ── Step 4: Compute overall confidence & save ─────────────────────
            overall_conf = 0.0
            if ai_result.confidence_scores:
                overall_conf = (
                    sum(c.confidence_score for c in ai_result.confidence_scores)
                    / len(ai_result.confidence_scores)
                )

            job.overall_confidence = overall_conf
            job.ai_engine = ai_result.engine_name
            job.ai_model = ai_result.model_name
            job.ocr_engine = ocr_result.engine_name

            # ── Phase 2: close out the pipeline run + one FIELD_EXTRACTIONS row per field
            pipeline_run.status = "COMPLETED"
            pipeline_run.completed_at = datetime.datetime.now(datetime.timezone.utc)
            pipeline_run.overall_confidence = overall_conf
            _record_event(db, job.id, "PIPELINE_COMPLETED", pipeline_run_id=pipeline_run.id, status="COMPLETED")

            field_rows = build_field_extraction_rows(job.id, pipeline_run.id, ai_result)
            for row in field_rows:
                db.add(FieldExtraction(**row))

            # Phase 9: flag only the specific fields below threshold — never the
            # whole document — as their own events, so this is queryable without
            # reading application logs (same acceptance bar as Phase 2/14).
            flagged = [r["field_name"] for r in field_rows if r["fallback_method"] == "needs_review"]
            if flagged:
                _record_event(
                    db, job.id, "FIELD_NEEDS_REVIEW",
                    pipeline_run_id=pipeline_run.id,
                    status="WARNING",
                    message=f"{len(flagged)} field(s) below confidence threshold",
                    metadata={"fields": flagged, "threshold": settings.CONFIDENCE_THRESHOLD},
                )

            db.add(ProcessingResult(
                job_id=job.id,
                extracted_json=ai_result.parsed_data,
                confidence_json=(
                    [c.model_dump() for c in ai_result.confidence_scores]
                    if ai_result.confidence_scores else []
                ),
                ocr_metadata_json=ocr_result.model_dump(),
            ))

            # Phase 10: a document with every field clean still finishes
            # COMPLETED; one with Phase 9 needs_review fields still finishes
            # (never blocked), but as WARNING rather than silently COMPLETED
            # — master prompt section 48's "partial failure -> warning, not
            # a false-positive success" rule, now reachable at the job level.
            job_status = "WARNING" if flagged else "COMPLETED"
            job.status = job_status
            job.completed_at = datetime.datetime.now(datetime.timezone.utc)
            if doc:
                doc.status = job_status
            _record_event(db, job.id, "DOCUMENT_COMPLETED", pipeline_run_id=pipeline_run.id, status=job_status)
            db.commit()

            total_ms = int((time.perf_counter() - t_total) * 1000)
            logger.info(
                "extractor_success",
                job_id=job_id,
                confidence=overall_conf,
                total_ms=total_ms,
                render_ms=render_ms,
                ocr_ms=ocr_ms,
                ai_ms=ai_ms,
                pages=len(pages),
                workers=max_workers,
            )

        except Exception as exc:
            db.rollback()
            err_msg = f"{str(exc)}\n{traceback.format_exc()}"

            # Phase 10: only a transient infra error, AND only while this job's
            # bounded Retry policy still has attempts left, gets re-raised so
            # RQ requeues it. Everything else (deterministic errors, or the
            # BackgroundTasks fallback which has no Retry concept at all) is
            # marked FAILED immediately, exactly as before this phase.
            if _is_transient_error(exc) and _rq_retries_remaining():
                logger.warning("extractor_transient_failure_will_retry", job_id=job_id, error=err_msg)
                try:
                    _record_event(
                        db, job_id, "PIPELINE_FAILED",
                        status="WARNING",
                        message=f"transient error, will retry: {str(exc)[:500]}",
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                raise

            logger.error("extractor_failed", job_id=job_id, error=err_msg)
            _mark_failed(job_id, str(exc))

