"""
PageReader: turn page i of a document into PageData with ONE read.

  digital PDF page -> words straight from the text layer (no OCR)
  scanned page     -> cache hit, or one Tesseract pass (then cached)

Every later step (key:value scan, tables, evidence) uses these words; none of
them starts its own full-page OCR.
"""
from __future__ import annotations

import time

from PIL import Image

from app.core.logging import get_logger
from app.fast.ocr_cache import OcrCache, tesseract_version
from app.fast.triage import DocumentSource
from app.fast.types import PageData

logger = get_logger(__name__)


class PageReader:
    def __init__(self, source: DocumentSource | None, cache: OcrCache, ocr_engine=None):
        self.source = source
        self.cache = cache
        self._engine = ocr_engine

    def _ocr(self):
        if self._engine is None:
            from app.core.model_registry import get_tesseract_engine
            self._engine = get_tesseract_engine()
        if not getattr(self._engine, "_is_loaded", True):
            self._engine.load_model()      # resolves the tesseract binary path
        return self._engine

    def read(self, index: int) -> PageData:
        t0 = time.perf_counter()
        image, native_lines, scale = self.source.load_page(index)
        t_load = time.perf_counter()
        page_no = index + 1
        ms = {"load_render": int((t_load - t0) * 1000)}
        warnings: list[str] = []

        if native_lines is not None:
            lines, src = native_lines, "native"
        else:
            key = OcrCache.make_key(self.source.sha256, page_no, scale, "tesseract", tesseract_version())
            lines = self.cache.get(key)
            if lines is not None:
                src = "ocr_cache"
            else:
                t_ocr = time.perf_counter()
                lines = self._ocr()._ocr_image(image, index)
                ms["ocr"] = int((time.perf_counter() - t_ocr) * 1000)
                src = "ocr"
                if lines:
                    self.cache.put(key, lines)
                else:
                    warnings.append("ocr_returned_no_text")
        ms["total"] = int((time.perf_counter() - t0) * 1000)
        logger.info("fast_page_read", page=page_no, source=src, lines=len(lines), **{f"{k}_ms": v for k, v in ms.items()})
        return PageData(
            page_number=page_no, width=image.width, height=image.height, scale=scale,
            source=src, lines=lines, image=image, ms=ms, warnings=warnings,
        )

    def from_image(self, image: Image.Image, page_no: int) -> PageData:
        """For callers that only have a rendered image (the generic pipeline
        interface): OCR it once; cached by pixel hash."""
        import hashlib
        t0 = time.perf_counter()
        digest = hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()
        key = OcrCache.make_key(digest, page_no, 1.0, "tesseract", tesseract_version())
        lines = self.cache.get(key)
        src = "ocr_cache"
        warnings: list[str] = []
        if lines is None:
            lines = self._ocr()._ocr_image(image, page_no - 1)
            src = "ocr"
            if lines:
                self.cache.put(key, lines)
            else:
                warnings.append("ocr_returned_no_text")
        return PageData(
            page_number=page_no, width=image.width, height=image.height, scale=1.0,
            source=src, lines=lines, image=image,
            ms={"total": int((time.perf_counter() - t0) * 1000)}, warnings=warnings,
        )
