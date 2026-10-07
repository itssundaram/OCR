"""
DOCINT — Legacy OCR Pipeline (Phase 4 of the implementation plan)

Adapter around the EXISTING hybrid OCR path (the `DIRECT_LLM_CALL=False`
branch in app/services/extractor.py: native-text/Tesseract/Surya dispatch
driven by settings.OCR_STRATEGY, plus img2table for tables). This wraps that
already-working logic behind the OCRPipeline contract; it does not
reimplement it.

NOT YET wired into process_document_job's main flow — see
services/extractor.py's module docstring and settings.USE_PIPELINE_REGISTRY.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image

from app.ai.orchestrator import ai_orchestrator
from app.core.config import settings
from app.ocr.base import OCRResult, OCRPage
from app.pipelines.base import (
    FieldResult,
    OCRPipeline,
    PipelineCapabilities,
    PipelineResult,
    TableResult,
)
from app.pipelines.registry import register_pipeline


@register_pipeline("legacy_ocr")
class LegacyOCRPipeline(OCRPipeline):
    """Native-text / Tesseract / Surya hybrid path — this is DOCINT's actual
    production pipeline today (settings.OCR_STRATEGY="surya" in .env)."""

    @property
    def name(self) -> str:
        return "legacy_ocr"

    @property
    def capabilities(self) -> PipelineCapabilities:
        return PipelineCapabilities(
            supports_layout_detection=settings.SURYA_LAYOUT_ENABLED,
            supports_tables=True,
            supports_handwriting=settings.HANDWRITING_FALLBACK_ENABLED,
            supports_vision=False,
            requires_gpu=settings.OCR_STRATEGY in ("surya", "surya_with_fallback"),
            model_name=settings.OCR_STRATEGY,
        )

    def preprocess(self, image: Image.Image, config: dict) -> Image.Image:
        # _process_single_page applies preprocess_page internally only on the
        # Surya branch (native-text and tesseract-only pages skip it) — this
        # method exists to satisfy the interface; the real per-page decision
        # still lives in _process_single_page until that function itself is
        # split apart (a later, non-urgent cleanup, not part of this phase).
        from app.ocr.preprocessing import preprocess_page
        return preprocess_page(image)

    def detect_regions(self, image: Image.Image) -> list[dict]:
        if not settings.SURYA_LAYOUT_ENABLED:
            return [{"type": "page", "bbox": {"x1": 0, "y1": 0, "x2": image.width, "y2": image.height}}]
        from app.ocr.surya_layout_engine import SuryaLayoutEngine
        regions = SuryaLayoutEngine().detect_layout(image)
        return [
            {"type": r.label, "bbox": {"x1": r.bbox.x1, "y1": r.bbox.y1, "x2": r.bbox.x2, "y2": r.bbox.y2}}
            for r in regions
        ]

    def extract_tables(self, pages: list[Image.Image], job_id: str) -> list[TableResult]:
        # Tables are already extracted per-page inside _process_single_page
        # (img2table) and folded into the OCRResult consumed by extract_fields;
        # this pipeline does not produce a second, separate table pass.
        return []

    def extract_fields(
        self,
        pages: list[Image.Image],
        template_fields: list[dict],
        job_id: str,
        *,
        native_texts: list[str] | None = None,
        schema: dict | None = None,
        department: str = "GENERAL",
        doc_type: str = "UNKNOWN",
        template_version: int = 1,
        extraction_instructions: str | None = None,
    ) -> PipelineResult:
        # Lazy import — avoids a circular import with services.extractor, and
        # matches this module's existing lazy-import style for heavy/optional deps.
        from app.services.extractor import _process_single_page

        texts = native_texts or [""] * len(pages)
        max_workers = min(len(pages), settings.OCR_MAX_WORKERS) if pages else 0
        page_results: dict[int, tuple] = {}

        with ThreadPoolExecutor(max_workers=max_workers or 1) as executor:
            future_to_idx = {
                executor.submit(_process_single_page, i, img, texts[i] if i < len(texts) else ""): i
                for i, img in enumerate(pages)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    result = future.result()
                    page_results[result[0]] = result[1:]
                except Exception as page_err:
                    page_results[idx] = ([], [], False, 0.0, "failed", pages[idx].width, pages[idx].height)

        ocr_pages: list[OCRPage] = []
        for i in sorted(page_results.keys()):
            lines, tables, is_native, avg_conf, ocr_method, width, height = page_results[i]
            ocr_pages.append(OCRPage(
                page_number=i + 1,
                width=width,
                height=height,
                lines=lines,
                tables=tables,
                is_native_text=is_native,
                extra_metadata={"avg_confidence": avg_conf, "method": ocr_method},
            ))

        ocr_method_overall = "native_text" if all(p.is_native_text for p in ocr_pages) else settings.OCR_STRATEGY
        ocr_result = OCRResult(
            pages=ocr_pages,
            engine_name="parallel_pipeline",
            engine_version="2.0",
            ocr_method=ocr_method_overall,
        )

        ai_result = ai_orchestrator.run_smart_extraction(
            ocr_result,
            schema or {},
            doc_id=job_id,
            department=department,
            doc_type=doc_type,
            template_version=template_version,
            extraction_instructions=extraction_instructions,
            page_images=pages,
        )

        conf_by_field = {c.field_name: c.confidence_score for c in ai_result.confidence_scores}
        envelope = ai_result.parsed_data or {}
        raw_fields = envelope.get("fields", envelope)
        fields = []
        for field_name, field_data in raw_fields.items():
            if field_name == "tables":
                continue
                
            if isinstance(field_data, dict) and "value" in field_data:
                field_val = field_data.get("value")
                field_conf = field_data.get("confidence")
            else:
                field_val = field_data
                field_conf = conf_by_field.get(field_name)

            fields.append(
                FieldResult(
                    field_name=field_name,
                    value="" if field_val is None else str(field_val),
                    confidence=field_conf or 0.0,
                    page_number=1,  # same per-field page-attribution gap as QwenPipeline today
                    extraction_method=ai_result.engine_name,
                )
            )

        overall_conf = (
            sum(c.confidence_score for c in ai_result.confidence_scores) / len(ai_result.confidence_scores)
            if ai_result.confidence_scores else 0.0
        )

        return PipelineResult(
            pipeline_name=self.name,
            status="completed",
            fields=fields,
            overall_confidence=overall_conf,
            metadata={
                "ai_engine": ai_result.engine_name,
                "ai_model": ai_result.model_name,
                "ocr_method": ocr_method_overall,
            },
        )
