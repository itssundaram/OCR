"""
Gap fill: read the ink that the main OCR pass skipped.

Why it exists. The main pass is one Tesseract run with --psm 6 ("one uniform block of text"). That is the
right setting for printed forms, but it silently drops text that does not fit the block model: a short
label in a box, a stamp or a date written beside the form, text on a shaded or dark cell, a line that sits
close to a ruling line, small isolated words. Those places look "obvious" to a person and were simply never
read, so no later step (key:value pairing, field matching, VLM crops) could ever see them.

What it does, generically (nothing here knows about any document type):
  1. mark every pixel of ink that is NOT inside a word box the first pass produced (ruling lines removed)
  2. merge the left-over ink into word/line-sized regions and keep the ones that look like text
  3. re-read just those regions with a mode that suits them (single line / sparse text, upscaled,
     inverted when the region is dark) and add the words that come back with usable confidence

It is time-boxed and capped per page, never raises, and returns the original lines untouched when
nothing is found. Added words carry the same OCRWord/OCRLine types as the first pass.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

from app.core.config import settings
from app.core.logging import get_logger
from app.ocr.base import BoundingBox, OCRLine, OCRWord

logger = get_logger(__name__)

MIN_WORD_CONF = 0.55      # a gap word below this is noise more often than text
MAX_REGIONS = 8           # per page, biggest first
MAX_INK_GRAY = 150        # median gray of a region's ink above this = faint (watermark / show-through), not text


@dataclass
class GapStats:
    regions: int = 0
    read: int = 0
    words_added: int = 0
    ms: int = 0


def _ink_mask(gray: np.ndarray) -> np.ndarray:
    """Binary ink (255) with long ruling lines taken out, so box borders are not mistaken for text."""
    h, w = gray.shape
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 21, 12)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w // 20), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, h // 20)))
    lines = cv2.bitwise_or(cv2.morphologyEx(binary, cv2.MORPH_OPEN, hk), cv2.morphologyEx(binary, cv2.MORPH_OPEN, vk))
    lines = cv2.dilate(lines, np.ones((3, 3), np.uint8), iterations=1)
    return cv2.bitwise_and(binary, cv2.bitwise_not(lines))


def find_gap_regions(image: Image.Image, lines: list[OCRLine], max_regions: int = MAX_REGIONS) -> list[tuple[int, int, int, int]]:
    """Boxes (x1,y1,x2,y2) of text-like ink not covered by any OCR word, biggest first."""
    gray = np.array(image.convert("L"))
    h, w = gray.shape
    ink = _ink_mask(gray)

    covered = np.zeros_like(ink)
    for ln in lines:
        for wd in ln.words:
            b = wd.bbox
            if b is None:
                continue
            padx, pady = 3, 2
            covered[max(0, int(b.y1) - pady):min(h, int(b.y2) + pady), max(0, int(b.x1) - padx):min(w, int(b.x2) + padx)] = 255
    residual = cv2.bitwise_and(ink, cv2.bitwise_not(covered))

    # merge characters into word / short-line blobs
    kx = max(9, w // 110)
    ky = max(3, h // 420)
    merged = cv2.dilate(residual, cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky)), iterations=1)
    n, _, stats, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)

    min_h = max(10, int(h * 0.0045))
    max_h = int(h * 0.12)
    out: list[tuple[int, int, int, int, int]] = []
    for i in range(1, n):
        x, y, bw, bh, _area = stats[i]
        if bh < min_h or bh > max_h or bw < 18 or bw > w * 0.9:
            continue
        ink_px = int(cv2.countNonZero(residual[y:y + bh, x:x + bw]))
        fill = ink_px / float(bw * bh)
        if ink_px < 40 or fill < 0.04 or fill > 0.65:      # specks / solid blobs are not text
            continue
        if bw / float(bh) < 0.5:                           # thin vertical smudge
            continue
        # faint ink (diagonal watermarks, scanner show-through): real print is much darker than the paper
        ink_gray = gray[y:y + bh, x:x + bw][residual[y:y + bh, x:x + bw] > 0]
        if ink_gray.size and float(np.median(ink_gray)) > MAX_INK_GRAY:
            continue
        # dashed / dotted separators and underlines: the ink itself is only a few pixels tall
        rows = np.where(residual[y:y + bh, x:x + bw].any(axis=1))[0]
        ink_h = int(rows[-1] - rows[0] + 1) if rows.size else 0
        if ink_h <= max(6, int(h * 0.0035)) and bw / float(max(1, ink_h)) > 10:
            continue
        out.append((x, y, x + bw, y + bh, bw * bh))
    out.sort(key=lambda r: -r[4])
    return [(a, b, c, d) for a, b, c, d, _ in out[:max_regions]]


def _erase_rules(crop: Image.Image) -> Image.Image:
    """Take out ruling lines that cross the crop (a table line through a word is the usual reason a cell is misread)."""
    g = np.array(crop)
    if g.shape[0] < 8 or g.shape[1] < 8:
        return crop
    binary = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 21, 12)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, int(g.shape[1] * 0.5)), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, int(g.shape[0] * 0.6))))
    mask = cv2.bitwise_or(cv2.morphologyEx(binary, cv2.MORPH_OPEN, hk), cv2.morphologyEx(binary, cv2.MORPH_OPEN, vk))
    if not mask.any():
        return crop
    g[cv2.dilate(mask, np.ones((3, 3), np.uint8)) > 0] = 255
    return Image.fromarray(g)


def _flatten_shading(gray: Image.Image) -> Image.Image:
    """Text on a grey / coloured box: Tesseract's global threshold turns the whole box dark or the text pale and
    drops the value. A local (adaptive) threshold keeps the strokes and turns the shading white."""
    g = np.array(gray)
    if g.shape[0] < 12 or g.shape[1] < 12:
        return gray
    block = max(15, (min(g.shape) // 2) | 1)
    out = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 12)
    return Image.fromarray(out)


def _prepare_crop(image: Image.Image, box: tuple[int, int, int, int], flatten: bool = False) -> tuple[Image.Image, float]:
    x1, y1, x2, y2 = box
    pad = 8
    crop = image.crop((max(0, x1 - pad), max(0, y1 - pad), min(image.width, x2 + pad), min(image.height, y2 + pad))).convert("L")
    crop = _erase_rules(crop)
    if flatten:
        crop = _flatten_shading(crop)
    if float(np.asarray(crop).mean()) < 110:                # light text on a dark region
        crop = ImageOps.invert(crop)
    scale = 1.0
    if crop.height < 70:                                    # Tesseract is poor below ~20 px x-height
        scale = 2.0 if crop.height >= 35 else 3.0
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
    crop = ImageOps.expand(crop, border=12, fill=255)
    return crop.convert("RGB"), scale


def fill_gaps(image: Image.Image, lines: list[OCRLine], engine, budget_s: float | None = None) -> tuple[list[OCRLine], GapStats]:
    """Return (lines + newly read lines sorted top to bottom, stats). `lines` is never modified."""
    stats = GapStats()
    t0 = time.perf_counter()
    budget = float(budget_s if budget_s is not None else settings.FAST_GAP_FILL_BUDGET_S)
    try:
        boxes = find_gap_regions(image, lines)
    except Exception as exc:
        logger.warning("gapfill_detect_failed", error=str(exc))
        return lines, stats
    stats.regions = len(boxes)
    if not boxes:
        return lines, stats

    def _read(box):
        if time.perf_counter() - t0 > budget:
            return box, None, 1.0
        try:
            crop, scale = _prepare_crop(image, box)
            tall = (box[3] - box[1]) > 55
            got = engine._ocr_image(crop, 0, psm=6 if tall else 7)
            if not got and not tall:
                got = engine._ocr_image(crop, 0, psm=11)
            return box, got, scale
        except Exception as exc:
            logger.warning("gapfill_read_failed", error=str(exc))
            return box, None, 1.0

    added: list[OCRLine] = []
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="gapfill") as pool:
        results = list(pool.map(_read, boxes))
    for box, got, scale in results:
        if got is None:
            continue
        stats.read += 1
        ox, oy = max(0, box[0] - 8), max(0, box[1] - 8)
        for ln in got:
            words: list[OCRWord] = []
            for wd in ln.words:
                txt = (wd.text or "").strip()
                if not txt or wd.confidence is None or wd.confidence < MIN_WORD_CONF or wd.bbox is None:
                    continue
                if len(txt) < 2 and not txt.isalnum():
                    continue
                # undo: border (12 px), upscale, crop offset
                bx1 = (wd.bbox.x1 - 12) / scale + ox
                by1 = (wd.bbox.y1 - 12) / scale + oy
                bx2 = (wd.bbox.x2 - 12) / scale + ox
                by2 = (wd.bbox.y2 - 12) / scale + oy
                words.append(OCRWord(text=txt, bbox=BoundingBox(x1=bx1, y1=by1, x2=bx2, y2=by2), confidence=wd.confidence))
            if not words:
                continue
            lb = BoundingBox(x1=min(w.bbox.x1 for w in words), y1=min(w.bbox.y1 for w in words),
                             x2=max(w.bbox.x2 for w in words), y2=max(w.bbox.y2 for w in words))
            added.append(OCRLine(text=" ".join(w.text for w in words), words=words, bbox=lb,
                                 confidence=sum(w.confidence for w in words) / len(words)))
            stats.words_added += len(words)

    stats.ms = int((time.perf_counter() - t0) * 1000)
    if not added:
        return lines, stats
    merged = [*lines, *added]          # first-pass order is kept; consumers work from geometry
    return merged, stats
