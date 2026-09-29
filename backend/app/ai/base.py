"""
DOCINT — AI Extraction Base
Defines the abstract interfaces for LLM/VLM based extraction engines.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel, Field

from app.ocr.base import OCRResult


class FieldConfidence(BaseModel):
    field_name: str
    confidence_score: float  # 0.0 to 1.0
    reasoning: str | None = None


class ExtractionResult(BaseModel):
    """
    Standardized output from the AI extraction phase.
    """
    raw_json: str
    parsed_data: dict[str, Any]
    confidence_scores: list[FieldConfidence] = Field(default_factory=list)
    engine_name: str
    model_name: str
    total_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class AIExtractionEngine(ABC):
    """
    Abstract Base Class for LLM/VLM extraction engines.
    """

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """Name of the engine integration (e.g. 'ollama', 'transformers')"""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Checks if the underlying service/model is available."""
        pass

    @abstractmethod
    def extract(
        self,
        ocr_result: OCRResult,
        schema: dict[str, Any],
        system_prompt: str,
        user_prompt: str,
        images: list[str] | None = None
    ) -> ExtractionResult:
        """
        Executes the extraction process using the OCR text and target JSON schema.
        """
        pass
