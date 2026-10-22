"""Shared data types for the fast_ocr pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

from app.ocr.base import OCRLine, OCRWord


@dataclass
class PageData:
    """Everything later steps need from one page: words + boxes in PAGE PIXELS
    (the same coordinates as `image`), plus where the words came from."""
    page_number: int                 # 1-based
    width: int
    height: int
    scale: float                     # render scale used for `image`
    source: str                      # "native" | "ocr" | "ocr_cache"
    lines: list[OCRLine]
    image: Image.Image | None = None
    ms: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)

    def words(self) -> list[OCRWord]:
        return [w for ln in self.lines for w in ln.words]

    @property
    def mean_confidence(self) -> float:
        confs = [w.confidence for w in self.words() if w.confidence is not None]
        return sum(confs) / len(confs) if confs else 0.0
