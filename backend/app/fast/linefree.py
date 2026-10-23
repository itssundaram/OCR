"""
Ruling-line-free OCR input for scanned table pages.

Why: Tesseract reads the grid lines of a ruled table as characters ("|", "l",
"1", "_") and its word confidence collapses (measured on a 4-page cadet list:
mean 28 -> 66 after the lines were erased). Only the OCR *input* is changed;
the page image used for crops, boxes and the viewer is untouched, and when the
page had to be straightened the word boxes are rotated back into page pixels.

  prepare_for_ocr(image)     -> (image_for_ocr, info)   no-op without tables
  restore_lines(lines, info) -> lines in original page pixels
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from app.core.logging import get_logger
from app.ocr.base import BoundingBox, OCRLine, OCRWord
from app.ocr.table_regions import find_table_regions

logger = get_logger(__name__)

MIN_SKEW_DEG = 0.25      # below this the page is treated as straight
MAX_SKEW_DEG = 6.0       # above this the estimate is not trusted
OCR_TAG = "tesseract+tbl2+gap1"   # OCR-cache engine tag (bumped: gap-fill read added)


@dataclass
class PrepInfo:
    changed: bool = False
    skew_deg: float = 0.0
    regions: int = 0
    size: tuple[int, int] = (0, 0)


def _line_masks(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    h, w = gray.shape
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 10)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, w // 25), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, h // 25)))
    return (cv2.morphologyEx(binary, cv2.MORPH_OPEN, hk),
            cv2.morphologyEx(binary, cv2.MORPH_OPEN, vk))


def estimate_skew(gray: np.ndarray, horiz: np.ndarray | None = None) -> float:
    """Angle (degrees, positive = rules descend to the right) of the long horizontal rules."""
    if horiz is None:
        horiz, _ = _line_masks(gray)
    w = gray.shape[1]
    segs = cv2.HoughLinesP(horiz, 1, np.pi / 720, threshold=80, minLineLength=max(60, w // 6), maxLineGap=20)
    if segs is None:
        return 0.0
    angles, weights = [], []
    for x1, y1, x2, y2 in np.asarray(segs).reshape(-1, 4):
        dx, dy = float(x2 - x1), float(y2 - y1)
        if abs(dx) < 1:
            continue
        a = math.degrees(math.atan2(dy, dx))
        if abs(a) <= MAX_SKEW_DEG:
            angles.append(a)
            weights.append(math.hypot(dx, dy))
    if not angles:
        return 0.0
    order = np.argsort(angles)
    a_sorted = np.asarray(angles)[order]
    cum = np.cumsum(np.asarray(weights)[order])
    return float(a_sorted[int(np.searchsorted(cum, cum[-1] / 2))])   # length-weighted median


def prepare_for_ocr(image: Image.Image) -> tuple[Image.Image, PrepInfo]:
    """Erase ruling lines inside detected table regions (and straighten the
    page when it is visibly skewed). Pages without a ruled table come back as-is."""
    info = PrepInfo(size=image.size)
    try:
        regions = find_table_regions(image)
    except Exception as exc:                           # the detector must never fail a page read
        logger.warning("linefree_region_detect_failed", error=str(exc))
        return image, info
    if not regions:
        return image, info
    info.regions = len(regions)

    gray = np.array(image.convert("L"))
    horiz, vert = _line_masks(gray)
    skew = estimate_skew(gray, horiz)

    keep = np.zeros_like(horiz)
    for x1, y1, x2, y2 in regions:
        keep[max(0, y1):y2, max(0, x1):x2] = 255
    mask = cv2.bitwise_and(cv2.bitwise_or(horiz, vert), keep)
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    cleaned = gray.copy()
    cleaned[mask > 0] = 255
    out = Image.fromarray(cleaned).convert("RGB")
    info.changed = True

    if MIN_SKEW_DEG <= abs(skew) <= MAX_SKEW_DEG:
        out = out.rotate(skew, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
        info.skew_deg = round(skew, 3)
    return out, info


def _box_back(b: BoundingBox | None, info: PrepInfo) -> BoundingBox | None:
    """Map a box from the straightened image back to page pixels (centre is
    rotated, size kept: the angle is a few degrees at most)."""
    if b is None:
        return None
    w, h = info.size
    cx, cy = w / 2.0, h / 2.0
    t = math.radians(info.skew_deg)
    c, s = math.cos(t), math.sin(t)
    mx, my = (b.x1 + b.x2) / 2 - cx, (b.y1 + b.y2) / 2 - cy
    nx, ny = cx + mx * c - my * s, cy + mx * s + my * c
    hw, hh = (b.x2 - b.x1) / 2, (b.y2 - b.y1) / 2
    return BoundingBox(x1=nx - hw, y1=ny - hh, x2=nx + hw, y2=ny + hh)


def restore_lines(lines: list[OCRLine], info: PrepInfo) -> list[OCRLine]:
    if not info.skew_deg:
        return lines
    out: list[OCRLine] = []
    for ln in lines:
        words = [OCRWord(text=w.text, bbox=_box_back(w.bbox, info), confidence=w.confidence) for w in ln.words]
        out.append(OCRLine(text=ln.text, words=words, bbox=_box_back(ln.bbox, info), confidence=ln.confidence))
    return out
