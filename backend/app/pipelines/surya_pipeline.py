"""
DOCINT — Surya Pipeline (Phase 6 of the implementation plan)

Standalone adapter around the Surya layout + OCR + handwriting/Tesseract
fallback path, now extracted into app.services.extractor._run_surya_on_page
(Phase 6 refactor) so Surya is selectable per template/department through
the pipeline registry — independent of the global settings.OCR_STRATEGY
switch that silently gated it before. This wraps the EXISTING Surya engines
(SuryaLayoutEngine, SuryaOCREngine, HandwritingOCREngine / TesseractOCREngine,
region_processor) — no new OCR logic, same pattern as LegacyOCRPipeline and
QwenPipeline before it.

NOT YET wired into process_document_job's main flow — same staged,
non-cutover approach as the other two registered pipelines (see
services/extractor.py's module docstring). Per the plan's own risk note:
"Surya extraction carries real risk of behavior drift since it's being
pulled out of an embedded branch — budget time for side-by-side comparison
against the current inline behavior before switching any department over
to it." This adapter calls the exact same extracted function the live
`surya`/`surya_with_fallback` OCR_STRATEGY branch now calls, so there is no
new Surya code path to drift from — only a new way to select the existing one.
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


@register_pipeline("surya")
class SuryaPipeline(OCRPipeline):
    """Layout detection -> region-routed Surya OCR -> TrOCR/Tesseract
    low-confidence fallback. Selectable regardless of settings.OCR_STRATEGY —
    that global setting still drives the legacy inline dispatch in
    _process_single_page, but this pipeline always runs the Surya path."""

    @property
    def name(self) -> str:
        return "surya"

    @property
    def capabilities(self) -> PipelineCapabilities:
        return PipelineCapabilities(
            supports_layout_detection=settings.SURYA_LAYOUT_ENABLED,
            supports_tables=True,
            supports_handwriting=settings.HANDWRITING_FALLBACK_ENABLED,
            supports_vision=False,
            requires_gpu=True,
            model_name="surya",
        )

    def preprocess(self, image: Image.Image, config: dict) -> Image.Image:
        from app.ocr.preprocessing import preprocess_page
        return preprocess_page(image, config)

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
        # Table regions are detected and OCR'd inline by the region processor
        # (region_type == "Table") and folded into the per-page OCRResult
        # consumed by extract_fields — same pattern as LegacyOCRPipeline, no
        # separate table pass here.
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
        use_tesseract_fallback: bool = False,
    ) -> PipelineResult:
        # Lazy import — avoids a circular import with services.extractor,
        # matching the existing lazy-import style in legacy_ocr_pipeline.py.
        from app.services.extractor import _run_surya_on_page

        max_workers = min(len(pages), settings.OCR_MAX_WORKERS) if pages else 0
        page_results: dict[int, tuple] = {}

        with ThreadPoolExecutor(max_workers=max_workers or 1) as executor:
            future_to_idx = {
                executor.submit(_run_surya_on_page, img, use_tesseract_fallback=use_tesseract_fallback): i
                for i, img in enumerate(pages)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    lines, tables, avg_conf, ocr_method, width, height = future.result()
                    page_results[idx] = (lines, tables, avg_conf, ocr_method, width, height)
                except Exception:
                    page_results[idx] = ([], [], 0.0, "failed", pages[idx].width, pages[idx].height)

        ocr_pages: list[OCRPage] = []
        for i in sorted(page_results.keys()):
            lines, tables, avg_conf, ocr_method, width, height = page_results[i]
            ocr_pages.append(OCRPage(
                page_number=i + 1,
                width=width,
                height=height,
                lines=lines,
                tables=tables,
                is_native_text=False,
                extra_metadata={"avg_confidence": avg_conf, "method": ocr_method},
            ))

        ocr_result = OCRResult(
            pages=ocr_pages,
            engine_name="surya_pipeline",
            engine_version="1.0",
            ocr_method="surya",
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
        fields = [
            FieldResult(
                field_name=field_name,
                value="" if field_value is None else str(field_value),
                confidence=conf_by_field.get(field_name, 0.0),
                page_number=1,  # same per-field page-attribution gap as the other two adapters today
                extraction_method=ai_result.engine_name,
            )
            for field_name, field_value in (ai_result.parsed_data or {}).items()
        ]

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
                "ocr_method": "surya",
            },
        )
