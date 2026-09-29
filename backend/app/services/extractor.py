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
"""
from __future__ import annotations

import datetime
import time
import traceback
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import SessionLocal
from app.db.models import Document, ProcessingJob, Template, ProcessingResult
from app.ocr.image_extractor import extract_pages_as_images_with_metadata
from app.ocr.tesseract import TesseractOCREngine
# NOTE: img2table_engine is imported lazily inside _process_single_page().
# This avoids loading numba at startup (which may be blocked by Application
# Control policies) when DIRECT_LLM_CALL=True skips the OCR path entirely.
from app.ai.orchestrator import ai_orchestrator
from app.ocr.base import OCRResult, OCRPage, OCRLine, OCRTable

logger = get_logger(__name__)


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
            db.commit()


def _process_single_page(
    page_index: int,
    img: Image.Image,
    native_text: str,
) -> tuple[int, list[OCRLine], list[OCRTable], bool, float, str, int, int]:
    """
    Process one page: run Tesseract OCR and optionally img2table.
    Designed to run inside a ThreadPoolExecutor — Tesseract spawns its own
    subprocess so there is no Python GIL contention.

    Returns:
        (page_index, lines, tables, is_native, avg_conf, ocr_method, width, height)
    """
    width, height = img.width, img.height

    # ── img2table ─────────────────────────────────────────────────────────────
    # Always run img2table to extract structured tables, even for native-text PDFs.
    # Import lazily here so numba's DLL is never loaded when DIRECT_LLM_CALL=True.
    from app.ocr.img2table_engine import extract_tables_from_image  # noqa: PLC0415
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    extracted_tables = extract_tables_from_image(buf.getvalue())

    # ── OCR ───────────────────────────────────────────────────────────────────
    if native_text:
        # Native-text PDF page: PyMuPDF already extracted the text perfectly.
        # Wrap it in a single OCRLine with 100% confidence.
        lines: list[OCRLine] = [OCRLine(text=native_text, confidence=1.0)]
        is_native = True
        avg_conf = 1.0
        ocr_method = "native_text"
    else:
        # Scanned/photographed page: use Tesseract.
        # Each thread creates its own engine instance — Tesseract is a subprocess
        # so this is safe and does not share state.
        engine = TesseractOCREngine()
        res = engine.process_images([img])
        if res.pages and res.pages[0].lines:
            lines = res.pages[0].lines
            avg_conf = sum(ln.confidence for ln in lines) / len(lines)
        else:
            lines = []
            avg_conf = 0.0
        is_native = False
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
                ocr_method_overall = "native_text" if all(p.is_native_text for p in pages) else "tesseract"

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

            db.add(ProcessingResult(
                job_id=job.id,
                extracted_json=ai_result.parsed_data,
                confidence_json=(
                    [c.model_dump() for c in ai_result.confidence_scores]
                    if ai_result.confidence_scores else []
                ),
                ocr_metadata_json=ocr_result.model_dump(),
            ))

            job.status = "COMPLETED"
            job.completed_at = datetime.datetime.now(datetime.timezone.utc)
            if doc:
                doc.status = "COMPLETED"
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
            logger.error("extractor_failed", job_id=job_id, error=err_msg)
            _mark_failed(job_id, str(exc))

