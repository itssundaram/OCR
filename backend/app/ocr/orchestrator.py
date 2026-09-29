"""
DOCINT — OCR Orchestrator
Manages the lifecycle of OCR engines. Now exclusively uses Tesseract.
"""
from __future__ import annotations

from PIL import Image

from app.core.logging import get_logger
from app.core.exceptions import OCREngineUnavailableError
from app.ocr.base import OCREngine, OCRResult
from app.ocr.tesseract import TesseractOCREngine
from app.ocr.normalizer import OCRNormalizer

logger = get_logger(__name__)


class OCROrchestrator:
    def __init__(self) -> None:
        self._tesseract_engine: OCREngine | None = None

    def _get_tesseract(self) -> OCREngine:
        if not self._tesseract_engine:
            self._tesseract_engine = TesseractOCREngine()
        return self._tesseract_engine

    def process_images(self, images: list[Image.Image]) -> OCRResult:
        is_scanned = [True] * len(images)
        return self.process_images_with_metadata(images, is_scanned)

    def process_images_with_metadata(
        self,
        images: list[Image.Image],
        is_scanned: list[bool],
    ) -> OCRResult:
        strategy = "tesseract_only"
        logger.info("ocr_orchestrator_start", strategy=strategy, pages=len(images))

        try:
            raw_result = self._get_tesseract().process_images(images)
            normalized_result = OCRNormalizer.normalize_result(raw_result)
            logger.info("ocr_orchestrator_complete", strategy=strategy)
            return normalized_result

        except Exception as e:
            logger.error("ocr_orchestrator_failed", error=str(e), exc_info=True)
            raise OCREngineUnavailableError(f"{strategy} failed: {str(e)}")

# Global singleton
ocr_orchestrator = OCROrchestrator()
