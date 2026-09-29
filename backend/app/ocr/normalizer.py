"""
DOCINT — OCR Normalizer
Post-processes OCR results (cleaning text, merging broken lines).
"""
from __future__ import annotations

import re
from app.ocr.base import OCRResult, OCRPage


class OCRNormalizer:
    @staticmethod
    def clean_text(text: str) -> str:
        """Removes illegal characters and trims whitespace."""
        # Remove null bytes and unwanted control chars but keep newlines
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        return cleaned.strip()

    @staticmethod
    def normalize_result(result: OCRResult) -> OCRResult:
        """
        Applies standard cleanup to the entire OCR result.
        """
        for page in result.pages:
            for line in page.lines:
                line.text = OCRNormalizer.clean_text(line.text)
                for word in line.words:
                    word.text = OCRNormalizer.clean_text(word.text)
                    
        return result
