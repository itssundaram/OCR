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

from app.core.config import settings
from app.core.logging import get_logger
from app.fast.gapfill import fill_gaps
from app.fast.linefree import OCR_TAG, prepare_for_ocr, restore_lines
from app.fast.ocr_cache import OcrCache, tesseract_version
from app.fast.orientation import (accept_rotation, candidate_rotations, good_words, rotate_image, unrotate_xy)
from app.fast.triage import DocumentSource
from app.fast.types import PageData
from app.ocr.base import BoundingBox, OCRLine, OCRWord

logger = get_logger(__name__)


def _unrotate_box(b: BoundingBox | None, rot: int, w: int, h: int) -> BoundingBox | None:
    if b is None:
        return None
    (ax, ay), (bx, by) = unrotate_xy(b.x1, b.y1, rot, w, h), unrotate_xy(b.x2, b.y2, rot, w, h)
    return BoundingBox(x1=min(ax, bx), y1=min(ay, by), x2=max(ax, bx), y2=max(ay, by))


def _unrotate_line(ln: OCRLine, rot: int, w: int, h: int) -> OCRLine:
    words = [OCRWord(text=x.text, bbox=_unrotate_box(x.bbox, rot, w, h), confidence=x.confidence) for x in ln.words]
    return OCRLine(text=ln.text, words=words, bbox=_unrotate_box(ln.bbox, rot, w, h), confidence=ln.confidence)


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

    def _ocr_pass(self, image: Image.Image, index: int):
        """One OCR pass on the ruling-line-free (and straightened) page; boxes come back in this image's pixels."""
        prepared, info = prepare_for_ocr(image)
        engine = self._ocr()
        lines = engine._ocr_image(prepared, index)
        if settings.FAST_GAP_FILL_ENABLED:
            # one targeted second read of text-like ink the main pass skipped (see gapfill.py); never fails the page
            try:
                lines, gap = fill_gaps(prepared, lines, engine)
                if gap.regions:
                    logger.info("fast_gap_fill", page=index + 1, regions=gap.regions, read=gap.read,
                                words_added=gap.words_added, ms=gap.ms)
            except Exception as exc:
                logger.warning("fast_gap_fill_failed", page=index + 1, error=str(exc))
        if info.changed:
            logger.info("fast_ocr_prepared", page=index + 1, regions=info.regions, skew=info.skew_deg, flattened=info.flattened)
        return restore_lines(lines, info)

    def _ocr_clean(self, image: Image.Image, index: int):
        """Read the page upright-as-scanned; when its text runs sideways, also read it turned and keep the
        turn only if it yields clearly more confident words. Boxes always come back in the original pixels."""
        lines = self._ocr_pass(image, index)
        if not settings.FAST_ORIENTATION_ENABLED:
            return lines
        try:
            import numpy as np
            cand = candidate_rotations(np.array(image.convert("L")))
            if not cand.rotate_cw:
                return lines
            before = good_words(lines)
            best, best_good, best_rot = lines, before, 0
            for rot in [cand.rotate_cw] + list(cand.alt or []):
                turned = self._ocr_pass(rotate_image(image, rot), index)
                g = good_words(turned)
                if g > best_good:
                    best, best_good, best_rot = turned, g, rot
                if accept_rotation(before, best_good):
                    break                                   # clearly better: do not spend another pass
            logger.info("fast_orientation", page=index + 1, ratio=round(cand.ratio, 2), osd_conf=round(cand.osd_conf, 1),
                        good_upright=before, good_best=best_good, rotated=best_rot if accept_rotation(before, best_good) else 0)
            if best_rot and accept_rotation(before, best_good):
                w, h = image.size
                return [_unrotate_line(ln, best_rot, w, h) for ln in best]
        except Exception as exc:                          # orientation is an extra; never fail the page
            logger.warning("fast_orientation_failed", page=index + 1, error=str(exc)[:160])
        return lines

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
            key = OcrCache.make_key(self.source.sha256, page_no, scale, OCR_TAG, tesseract_version())
            lines = self.cache.get(key)
            if lines is not None:
                src = "ocr_cache"
            else:
                t_ocr = time.perf_counter()
                lines = self._ocr_clean(image, index)
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
        key = OcrCache.make_key(digest, page_no, 1.0, OCR_TAG, tesseract_version())
        lines = self.cache.get(key)
        src = "ocr_cache"
        warnings: list[str] = []
        if lines is None:
            lines = self._ocr_clean(image, page_no - 1)
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
