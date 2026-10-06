"""
DOCINT — Qwen / Direct-LLM Pipeline (Phase 4, Phase 8 of the implementation plan)

Adapter around the EXISTING Direct-LLM code path (the `DIRECT_LLM_CALL=True`
branch in app/services/extractor.py) — this does not reimplement extraction,
it wraps the already-working ai/orchestrator.py + ai/ollama.py chain behind
the OCRPipeline contract so it can be addressed through the registry.

NOT YET wired into process_document_job's main flow — see
services/extractor.py's module docstring and settings.USE_PIPELINE_REGISTRY.
Cutting the live job over to dispatch through this pipeline is a deliberately
separate, carefully-staged follow-up (shadow-mode comparison against the
current behavior first), per the plan's Phase 4 risk notes.
"""
from __future__ import annotations

from PIL import Image

from app.ai.orchestrator import ai_orchestrator
from app.ocr.base import OCRResult, OCRPage
from app.pipelines.base import (
    FieldResult,
    OCRPipeline,
    PipelineCapabilities,
    PipelineResult,
    TableResult,
)
from app.pipelines.registry import register_pipeline


@register_pipeline("qwen")
class QwenPipeline(OCRPipeline):
    """Vision-LLM fast path — no OCR, whole page image sent to the VLM."""

    @property
    def name(self) -> str:
        return "qwen"

    @property
    def capabilities(self) -> PipelineCapabilities:
        return PipelineCapabilities(
            supports_layout_detection=False,
            supports_tables=True,  # tables come back inside the LLM's structured JSON, not as separate crops
            supports_handwriting=True,  # vision models generally read handwriting directly
            supports_vision=True,
            requires_gpu=False,  # inference happens wherever Ollama is served, not necessarily locally
            model_name="qwen2.5vl:3b",
        )

    def preprocess(self, image: Image.Image, config: dict) -> Image.Image:
        # Matches current behavior: the direct-LLM path renders at a low DPI
        # and sends the image as-is — no deskew/denoise/etc. is applied.
        return image

    def detect_regions(self, image: Image.Image) -> list[dict]:
        # No layout detection in this pipeline — the whole page is the region,
        # since the VLM reads the full image rather than cropped fields.
        return [{"type": "page", "bbox": {"x1": 0, "y1": 0, "x2": image.width, "y2": image.height}}]

    def extract_tables(self, pages: list[Image.Image], job_id: str) -> list[TableResult]:
        # Tables are extracted as part of extract_fields' single LLM call, not
        # as a separate step — matches current behavior (no TableExtraction
        # rows are produced by the direct-LLM path today).
        return []

    def extract_fields(
        self,
        pages: list[Image.Image],
        template_fields: list[dict],
        job_id: str,
        *,
        native_texts: list[str] | None = None,  # unused — the vision model reads the image directly
        schema: dict | None = None,
        department: str = "GENERAL",
        doc_type: str = "UNKNOWN",
        template_version: int = 1,
        extraction_instructions: str | None = None,
    ) -> PipelineResult:
        ocr_pages = [
            OCRPage(
                page_number=i + 1,
                width=img.width,
                height=img.height,
                lines=[],
                tables=[],
                is_native_text=False,
                extra_metadata={"avg_confidence": 0.0, "method": "direct_llm"},
            )
            for i, img in enumerate(pages)
        ]
        ocr_result = OCRResult(
            pages=ocr_pages,
            engine_name="direct_llm",
            engine_version="1.0",
            ocr_method="direct_llm",
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
                page_number=1,  # Phase 6/16 territory: this pipeline doesn't yet attribute a field to a specific page
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
            metadata={"ai_engine": ai_result.engine_name, "ai_model": ai_result.model_name},
        )
