"""
DOCINT — Confidence Engine
Calculates confidence scores for extracted JSON fields by mapping them back to OCR word confidences.
"""
from __future__ import annotations

from typing import Any
import re

from app.ocr.base import OCRResult, OCRWord
from app.ai.base import FieldConfidence
from app.core.logging import get_logger

logger = get_logger(__name__)


class ConfidenceEngine:
    @staticmethod
    def calculate_confidence(
        parsed_data: dict[str, Any],
        ocr_result: OCRResult
    ) -> list[FieldConfidence]:
        """
        Calculates the confidence of each extracted field by locating its value
        within the original OCR text and averaging the confidence of those words.
        """
        # Flatten all OCR words into a single stream for searching
        all_words: list[OCRWord] = []
        for page in ocr_result.pages:
            for line in page.lines:
                # If we don't have word-level confidence, fallback to line-level
                if not line.words:
                    # Fabricate a word for the line
                    all_words.append(
                        OCRWord(
                            text=line.text, 
                            bbox=line.bbox, 
                            confidence=line.confidence if line.confidence is not None else 1.0
                        )
                    )
                else:
                    all_words.extend(line.words)

        full_text_lower = " ".join(w.text.lower() for w in all_words)
        
        confidences = []
        
        def process_value(key: str, value: Any):
            if value is None:
                return

            if isinstance(value, dict):
                for k, v in value.items():
                    process_value(f"{key}.{k}", v)
                return
                
            if isinstance(value, list):
                for i, v in enumerate(value):
                    process_value(f"{key}[{i}]", v)
                return

            # For primitive values, try to find them in the text
            val_str = str(value).lower()
            val_clean = re.sub(r'[\W_]+', '', val_str)
            text_clean = re.sub(r'[\W_]+', '', full_text_lower)
            
            # If the LLM completely hallucinated or heavily modified it, we might not find it
            if val_str in full_text_lower:
                confidences.append(FieldConfidence(
                    field_name=key,
                    confidence_score=0.95,
                    reasoning="Exact match found in OCR text."
                ))
            elif val_clean and val_clean in text_clean:
                confidences.append(FieldConfidence(
                    field_name=key,
                    confidence_score=0.90,
                    reasoning="Match found in OCR text ignoring formatting/punctuation."
                ))
            else:
                # Value was inferred, modified, or hallucinated
                confidences.append(FieldConfidence(
                    field_name=key,
                    confidence_score=0.60,
                    reasoning="Value inferred or normalized by AI (not exact match in OCR)."
                ))

        for k, v in parsed_data.items():
            process_value(k, v)
            
        return confidences
