"""
DOCINT — AI Orchestrator
Simplified linear extraction pipeline:
  1. Receive OCR/table text + template
  2. Send text and/or images to LLM
  3. Validate JSON and compute confidence
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.exceptions import AIProcessingError
from app.ocr.base import OCRResult
from app.ai.base import AIExtractionEngine, ExtractionResult
from app.ai.ollama import OllamaExtractionEngine
from app.ai.prompt_builder import PromptBuilder
from app.ai.validator import JSONValidator
from app.ai.confidence import ConfidenceEngine
from app.ai.response_builder import ResponseBuilder

logger = get_logger(__name__)


class AIOrchestrator:
    def __init__(self) -> None:
        self._ollama_engine: AIExtractionEngine | None = None

    def _get_engine(self) -> AIExtractionEngine:
        if not self._ollama_engine:
            self._ollama_engine = OllamaExtractionEngine()
        return self._ollama_engine

    def run_smart_extraction(
        self,
        ocr_result: OCRResult,
        schema: dict[str, Any],
        doc_id: str | None = None,
        department: str = "GENERAL",
        doc_type: str = "UNKNOWN",
        template_version: int = 1,
        extraction_instructions: str | None = None,
        page_images: list[Any] | None = None,  # list of PIL.Image.Image
    ) -> ExtractionResult:
        """
        Direct LLM extraction pipeline.
        Bypasses any previous RAG or fuzzy matching attempts.
        """
        engine = self._get_engine()
        # Pass extraction_instructions so templates can carry document-type-specific
        # guidance without hardcoding anything in the prompt builder.
        system_prompt = PromptBuilder.build_system_prompt(schema, extraction_instructions=extraction_instructions)

        payload_mode = getattr(settings, "LLM_PAYLOAD_MODE", "both")
        include_text = payload_mode in ("text", "both")
        include_image = payload_mode in ("image", "both")

        # Pass the entire text (which includes markdown tables) to the LLM
        user_prompt = PromptBuilder.build_user_prompt(
            ocr_result, 
            rag_context=None,  # No RAG context
            include_text=include_text
        )

        base64_images: list[str] | None = None
        if include_image and page_images:
            try:
                import base64
                import io
                from PIL import Image

                base64_images = []
                max_px = getattr(settings, "LLM_IMAGE_MAX_PX", 800)

                for img in page_images:
                    # Resize to save LLM tokens (LANCZOS is high quality)
                    img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    buf = io.BytesIO()
                    # JPEG with quality=85 is a good balance for vision models
                    img.save(buf, format="JPEG", quality=85)
                    base64_images.append(base64.b64encode(buf.getvalue()).decode("utf-8"))
                        
                logger.info("llm_images_prepared", count=len(base64_images), max_px=max_px, mode=payload_mode)
            except Exception as e:
                logger.warning("llm_image_preparation_failed", error=str(e))

        logger.info("llm_extraction_start", engine=engine.engine_name, pages=len(ocr_result.pages), payload_mode=payload_mode, direct_llm=settings.DIRECT_LLM_CALL)

        try:
            result = engine.extract(
                ocr_result=ocr_result, 
                schema=schema, 
                system_prompt=system_prompt, 
                user_prompt=user_prompt,
                images=base64_images
            )

            is_valid, errors = JSONValidator.validate(result.parsed_data, schema)
            if not is_valid:
                logger.warning("llm_validation_failed", errors=errors)

            result.confidence_scores = ConfidenceEngine.calculate_confidence(
                parsed_data=result.parsed_data,
                ocr_result=ocr_result,
            )

            overall_conf = 0.0
            if result.confidence_scores:
                overall_conf = sum(c.confidence_score for c in result.confidence_scores) / len(result.confidence_scores)

            response_json = ResponseBuilder.from_llm_json(
                llm_data=result.parsed_data,
                schema=schema,
                department=department,
                doc_type=doc_type,
                template_version=template_version,
                pages_processed=len(ocr_result.pages),
                overall_confidence=overall_conf,
                ocr_method=ocr_result.ocr_method,
                field_confidences=result.confidence_scores,
            )
            result.parsed_data = response_json

            logger.info("llm_extraction_complete", confidence=overall_conf)
            return result

        except Exception as e:
            logger.error("llm_extraction_failed", error=str(e), exc_info=True)
            raise AIProcessingError(engine.engine_name, str(e))

# Singleton instance
ai_orchestrator = AIOrchestrator()
