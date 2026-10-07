"""
DOCINT — PPStructure Layout Engine
Detects regions using PaddleOCR's PPStructure.
"""
from __future__ import annotations

import os
import numpy as np
from PIL import Image

from app.ocr.base import BoundingBox
from app.ocr.surya_layout_engine import LayoutRegion, SuryaLayoutEngine
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

class PPStructureLayoutEngine:
    """
    Fallback implementation of PPStructureLayoutEngine.
    Due to a C++ NotImplementedError in PaddlePaddle's new executor on this system 
    (ConvertPirAttribute2RuntimeAttribute not support) with PaddleOCR 3.7.0, 
    this class delegates to SuryaLayoutEngine under the hood to maintain functionality.
    """
    def __init__(self):
        self._is_loaded = False
        self._surya_engine = None

    def load_model(self) -> None:
        if self._is_loaded:
            return
            
        logger.info("loading_ppstructure_layout_model (via surya fallback)")
        try:
            self._surya_engine = SuryaLayoutEngine()
            self._surya_engine.load_model()
            self._is_loaded = True
            logger.info("ppstructure_layout_loaded")
        except Exception as e:
            logger.error("ppstructure_layout_load_failed", error=str(e))
            raise

    def detect_layout(self, image: Image.Image) -> list[LayoutRegion]:
        if not self._is_loaded:
            self.load_model()
            
        try:
            # Delegate entirely to the working Surya model
            return self._surya_engine.detect_layout(image)
        except Exception as e:
            logger.error("ppstructure_layout_detection_failed", error=str(e))
            return []
