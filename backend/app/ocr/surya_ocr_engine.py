"""
DOCINT — Surya OCR Engine
Implements the OCREngine interface using surya-ocr (>= 0.6.0).
"""
from __future__ import annotations

import io
from PIL import Image
import re

from app.core.config import settings
from app.ocr.base import OCREngine, OCRResult, OCRPage, OCRLine, BoundingBox
from app.core.logging import get_logger

logger = get_logger(__name__)

class SuryaOCREngine(OCREngine):
    """
    Subprocess wrapper not needed; Surya runs natively in Python.
    Can process full pages or cropped regions.
    """

    def __init__(self) -> None:
        self._is_loaded = False
        self._recognition_predictor = None

    @property
    def engine_name(self) -> str:
        return "surya"

    @property
    def engine_version(self) -> str:
        return "2.0.0"

    def load_model(self) -> None:
        if self._is_loaded:
            return

        logger.info("loading_surya_ocr_models")
        try:
            from surya.recognition import RecognitionPredictor
            self._recognition_predictor = RecognitionPredictor()
            self._is_loaded = True
            logger.info("surya_ocr_models_loaded")
        except Exception as exc:
            logger.error("surya_ocr_load_failed", error=str(exc))
            raise RuntimeError(f"Failed to load Surya OCR models: {exc}")

    def _extract_text_from_html(self, html: str) -> str:
        """Surya OCR 2 returns text in an HTML representation for structure. Strip tags."""
        if not html:
            return ""
        # Simple tag stripping for line-level text
        text = re.sub(r'<[^>]+>', ' ', html)
        # unescape html entities if needed, but simple whitespace collapse is enough here
        return " ".join(text.split())

    def process_images(self, images: list[Image.Image]) -> OCRResult:
        """
        Process full pages.
        """
        if not self._is_loaded:
            self.load_model()

        logger.info("surya_processing_pages", pages=len(images))
        
        ocr_pages: list[OCRPage] = []
        try:
            # For full page, surya RecognitionPredictor handles layout implicitly if we just pass images
            predictions = self._recognition_predictor(images)
            
            for i, (img, pred) in enumerate(zip(images, predictions)):
                width, height = img.size
                
                ocr_lines = []
                for block in pred.blocks:
                    polygon = block.polygon
                    x_coords = [p[0] for p in polygon]
                    y_coords = [p[1] for p in polygon]
                    x1, y1 = min(x_coords), min(y_coords)
                    x2, y2 = max(x_coords), max(y_coords)
                    
                    text = self._extract_text_from_html(block.html)
                    if not text.strip():
                        continue
                        
                    ocr_lines.append(
                        OCRLine(
                            text=text,
                            bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                            confidence=block.confidence if block.confidence is not None else 1.0,
                        )
                    )
                
                page = OCRPage(
                    page_number=i + 1,
                    width=width,
                    height=height,
                    lines=ocr_lines,
                )
                ocr_pages.append(page)
                
        except Exception as e:
            logger.error("surya_processing_failed", error=str(e))
            
        return OCRResult(
            pages=ocr_pages,
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            ocr_method="surya"
        )

    def process_region(self, region_image: Image.Image) -> tuple[list[OCRLine], float]:
        """
        Process a single cropped region and return lines and average confidence.
        """
        if not self._is_loaded:
            self.load_model()
            
        try:
            predictions = self._recognition_predictor([region_image])
            
            if not predictions or not predictions[0].blocks:
                return [], 0.0
                
            pred = predictions[0]
            lines = []
            total_conf = 0.0
            count = 0
            
            for block in pred.blocks:
                polygon = block.polygon
                x_coords = [p[0] for p in polygon]
                y_coords = [p[1] for p in polygon]
                x1, y1 = min(x_coords), min(y_coords)
                x2, y2 = max(x_coords), max(y_coords)
                
                text = self._extract_text_from_html(block.html)
                if not text.strip():
                    continue
                    
                conf = block.confidence if block.confidence is not None else 1.0
                lines.append(
                    OCRLine(
                        text=text,
                        bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                        confidence=conf,
                    )
                )
                total_conf += conf
                count += 1
                
            avg_conf = total_conf / count if count > 0 else 0.0
            return lines, avg_conf
            
        except Exception as e:
            logger.error("surya_region_failed", error=str(e))
            return [], 0.0
