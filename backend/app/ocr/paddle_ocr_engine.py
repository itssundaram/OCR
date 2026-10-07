"""
DOCINT — PaddleOCR Engine
Implements the OCREngine interface using paddleocr.
"""
from __future__ import annotations

import os
import numpy as np
from PIL import Image

from app.core.config import settings
from app.ocr.base import OCREngine, OCRResult, OCRPage, OCRLine, BoundingBox
from app.core.logging import get_logger

logger = get_logger(__name__)

class PaddleOCREngine(OCREngine):
    def __init__(self) -> None:
        self._is_loaded = False
        self._ocr = None

    @property
    def engine_name(self) -> str:
        return "paddle"

    @property
    def engine_version(self) -> str:
        return "2.7.0"

    def load_model(self) -> None:
        if self._is_loaded:
            return

        logger.info("loading_paddleocr_models")
        try:
            from paddleocr import PaddleOCR
            use_gpu = settings.GPU_ENABLED

            device = "gpu:0" if settings.GPU_ENABLED else "cpu"

            self._ocr = PaddleOCR(
                use_angle_cls=True, 
                lang='en', 
                device=device
            )
            self._is_loaded = True
            logger.info("paddleocr_loaded")
        except Exception as exc:
            logger.error("paddleocr_load_failed", error=str(exc))
            raise RuntimeError(f"Failed to load PaddleOCR models: {exc}")

    def process_images(self, images: list[Image.Image]) -> OCRResult:
        if not self._is_loaded:
            self.load_model()

        logger.info("paddle_processing_pages", pages=len(images))
        
        ocr_pages: list[OCRPage] = []
        try:
            for i, img in enumerate(images):
                if img.mode != "RGB":
                    img = img.convert("RGB")
                    
                width, height = img.size
                img_np = np.array(img)
                
                # PaddleOCR expects BGR format (like cv2.imread)
                img_bgr = img_np[:, :, ::-1]
                
                result = self._ocr.ocr(img_bgr)
                
                # Debug dump block removed
                ocr_lines = []
                if result:
                    for res_dict in result:
                        if not isinstance(res_dict, dict):
                            continue
                        polys = res_dict.get('rec_polys', res_dict.get('dt_polys', []))
                        texts = res_dict.get('rec_texts', [])
                        scores = res_dict.get('rec_scores', [])
                        
                        for idx in range(len(polys)):
                            box = polys[idx]
                            text = texts[idx]
                            conf = float(scores[idx]) if idx < len(scores) else 0.0
                            
                            x_coords = [p[0] for p in box]
                            y_coords = [p[1] for p in box]
                            x1, y1 = min(x_coords), min(y_coords)
                            x2, y2 = max(x_coords), max(y_coords)
                            
                            if not text.strip():
                                continue
                                
                            ocr_lines.append(
                                OCRLine(
                                    text=text,
                                    bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                                    confidence=conf,
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
            logger.error("paddle_processing_failed", error=str(e))
            
        return OCRResult(
            pages=ocr_pages,
            engine_name=self.engine_name,
            engine_version=self.engine_version,
            ocr_method="paddle"
        )

    def process_region(self, region_image: Image.Image) -> tuple[list[OCRLine], float]:
        if not self._is_loaded:
            self.load_model()
            
        try:
            if region_image.mode != "RGB":
                region_image = region_image.convert("RGB")
                
            img_np = np.array(region_image)
            img_bgr = img_np[:, :, ::-1]
            result = self._ocr.ocr(img_bgr)
            
            if not result:
                return [], 0.0
                
            lines = []
            total_conf = 0.0
            count = 0
            
            for res_dict in result:
                if not isinstance(res_dict, dict):
                    continue
                polys = res_dict.get('rec_polys', res_dict.get('dt_polys', []))
                texts = res_dict.get('rec_texts', [])
                scores = res_dict.get('rec_scores', [])
                
                for idx in range(len(polys)):
                    box = polys[idx]
                    text = texts[idx]
                    conf = float(scores[idx]) if idx < len(scores) else 0.0
                    
                    x_coords = [p[0] for p in box]
                    y_coords = [p[1] for p in box]
                    x1, y1 = min(x_coords), min(y_coords)
                    x2, y2 = max(x_coords), max(y_coords)
                    
                    if not text.strip():
                        continue
                        
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
            logger.error("paddle_region_failed", error=str(e))
            return [], 0.0
