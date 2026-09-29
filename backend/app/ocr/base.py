"""
DOCINT — OCR Base Abstractions
Defines the standard data structures for OCR output and the Abstract Base Class for engines.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel, Field
from PIL import Image


class BoundingBox(BaseModel):
    """
    Normalized bounding box. Coordinates should be relative to page size (0.0 to 1.0)
    to remain resolution-independent, or explicitly tracked with page dimensions.
    Here we use absolute pixels for simplicity, but track page dimensions.
    """
    x1: float
    y1: float
    x2: float
    y2: float


class OCRWord(BaseModel):
    text: str
    bbox: BoundingBox | None = None
    confidence: float | None = None


class OCRLine(BaseModel):
    text: str
    words: list[OCRWord] = Field(default_factory=list)
    bbox: BoundingBox | None = None
    confidence: float | None = None


class OCRTable(BaseModel):
    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    markdown: str = ""


class OCRPage(BaseModel):
    page_number: int
    width: int
    height: int
    lines: list[OCRLine] = Field(default_factory=list)
    tables: list[OCRTable] = Field(default_factory=list)
    is_native_text: bool = False
    extra_metadata: dict[str, Any] = Field(default_factory=dict)  # engine-specific data
    
    @property
    def full_text(self) -> str:
        text = "\n".join(line.text for line in self.lines)
        if self.tables:
            for i, t in enumerate(self.tables):
                text += f"\n\n[TABLE_START table_{i + 1}]\n{t.markdown}\n[TABLE_END table_{i + 1}]"
        return text


class OCRResult(BaseModel):
    pages: list[OCRPage] = Field(default_factory=list)
    engine_name: str
    engine_version: str
    ocr_method: str = "mixed"
    
    @property
    def full_text(self) -> str:
        return "\n\n".join(page.full_text for page in self.pages)


class OCREngine(ABC):
    """Abstract Base Class for all OCR Engines (Surya, TrOCR, etc.)"""
    
    @property
    @abstractmethod
    def engine_name(self) -> str:
        pass
        
    @property
    @abstractmethod
    def engine_version(self) -> str:
        pass

    @abstractmethod
    def load_model(self) -> None:
        """
        Load models into memory. Should be idempotent (safe to call multiple times).
        """
        pass

    @abstractmethod
    def process_images(self, images: list[Image.Image]) -> OCRResult:
        """
        Run OCR on a list of PIL Images (pages).
        """
        pass
