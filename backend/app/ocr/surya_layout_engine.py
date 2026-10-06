"""
DOCINT — Surya Layout Engine
Detects regions (Text, Table, Form, Checkbox, etc.) in a page image.
"""
from __future__ import annotations

from dataclasses import dataclass
from PIL import Image

from app.ocr.base import BoundingBox
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

LAYOUT_REGION_TYPES = [
    "Text", "Title", "Table", "Figure", "Form", 
    "Checkbox", "Signature", "Caption", "Footnote",
    "List-item", "Page-header", "Page-footer", "Math"
]

@dataclass
class LayoutRegion:
    region_type: str
    bbox: BoundingBox
    confidence: float
    cropped_image: Image.Image


class SuryaLayoutEngine:
    def __init__(self):
        self._is_loaded = False
        self._layout_predictor = None

    def load_model(self) -> None:
        if self._is_loaded:
            return
            
        logger.info("loading_surya_layout_model")
        try:
            from surya.layout import LayoutPredictor
            self._layout_predictor = LayoutPredictor()
            self._is_loaded = True
            logger.info("surya_layout_loaded")
        except Exception as e:
            logger.error("surya_layout_load_failed", error=str(e))
            raise

    def detect_layout(self, image: Image.Image) -> list[LayoutRegion]:
        if not self._is_loaded:
            self.load_model()
            
        try:
            # LayoutPredictor returns a list of LayoutResult
            layout_predictions = self._layout_predictor([image])
            
            if not layout_predictions:
                return []
                
            pred = layout_predictions[0]
            regions = []
            
            for box in pred.bboxes:
                if box.confidence is not None and box.confidence < settings.SURYA_LAYOUT_CONFIDENCE_THRESHOLD:
                    continue
                    
                # Compute bbox from polygon
                polygon = box.polygon
                x_coords = [p[0] for p in polygon]
                y_coords = [p[1] for p in polygon]
                x1, y1 = min(x_coords), min(y_coords)
                x2, y2 = max(x_coords), max(y_coords)
                
                # Add some padding
                pad = 5
                w, h = image.size
                crop_x1 = max(0, int(x1) - pad)
                crop_y1 = max(0, int(y1) - pad)
                crop_x2 = min(w, int(x2) + pad)
                crop_y2 = min(h, int(y2) + pad)
                
                cropped_img = image.crop((crop_x1, crop_y1, crop_x2, crop_y2))
                
                region = LayoutRegion(
                    region_type=box.label,
                    bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                    confidence=box.confidence if box.confidence is not None else 1.0,
                    cropped_image=cropped_img
                )
                regions.append(region)
                
            return regions
            
        except Exception as e:
            logger.error("layout_detection_failed", error=str(e))
            return []
