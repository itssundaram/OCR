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
OCR_TAG = "tesseract+tbl2+gap1+sk2+fl1+or1"   # OCR-cache engine tag (bumped: sideways pages are also read turned)
FLATTEN_BELOW = 215       # median page background (0-255) under which shading is divided out before OCR
TEXT_SKEW_MIN_DEG = 1.0   # Tesseract copes with a degree of tilt; rotating (resampling) only pays off beyond that
TEXT_SKEW_MAX_DEG = 10.0
TEXT_SKEW_MIN_GAIN = 1.3  # tilted-vs-straight line sharpness the estimate must show before the page is rotated


@dataclass
class PrepInfo:
    changed: bool = False
    skew_deg: float = 0.0
    regions: int = 0
    size: tuple[int, int] = (0, 0)
    flattened: bool = False   # uneven / grey page background was divided out for the OCR pass


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


def estimate_text_skew(gray: np.ndarray) -> tuple[float, float]:
    """Tilt of the printed text lines, for pages WITHOUT ruled tables (phone photos, crooked scans).

    The page is flattened (shadows removed), reduced to word-sized specks, smeared into line bars and rotated
    through candidate angles; the angle where the bars are sharpest (largest row-profile contrast) wins.
    Returns (angle_deg, gain): positive = text descends to the right; gain = sharpness at that angle divided by
    sharpness at 0 degrees (1.0 means the page is already straight), so the caller can ignore weak estimates.
    """
    h0, w0 = gray.shape
    s = 1000.0 / max(h0, w0)
    g = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1.0 else gray
    bg = cv2.medianBlur(g, 31)
    flat = cv2.divide(g, bg, scale=255)
    b = cv2.adaptiveThreshold(flat, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 12)
    n, lab, st, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
    H, W = b.shape
    ok = np.zeros(n, dtype=bool)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        ok[i] = 3 <= h <= 0.06 * H and w <= 0.25 * W and a >= 6          # drops borders, stamps, rules and dust
    keep = (ok[lab] * 255).astype(np.uint8)
    keep = keep[int(.04 * H):int(.96 * H), int(.04 * W):int(.96 * W)]
    if keep.sum() == 0:
        return 0.0, 1.0
    keep = cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 1)))
    h, w = keep.shape

    def sharp(angle: float) -> float:
        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)            # same turn the page will get (counter-clockwise)
        prof = cv2.warpAffine(keep, M, (w, h)).sum(1, dtype=np.float64)
        return float(np.sum(np.diff(prof) ** 2))

    coarse = [(sharp(a), a) for a in np.arange(-TEXT_SKEW_MAX_DEG, TEXT_SKEW_MAX_DEG + 0.01, 0.5)]
    best_a = max(coarse)[1]
    fine = [(sharp(a), a) for a in np.arange(best_a - 0.5, best_a + 0.51, 0.1)]
    best_v, best_a = max(fine)
    return float(best_a), best_v / max(sharp(0.0), 1.0)


def flatten_illumination(image: Image.Image) -> tuple[Image.Image, bool]:
    """Divide out uneven lighting / a grey page tint (phone photos, dark photocopies).

    Tesseract binarises the whole page against one threshold, so a shadow gradient turns whole text lines into
    noise (measured on a phone photo: lines merged and unreadable until the page was flattened). The background
    is the large-window median of the page; the page is divided by it. Geometry is untouched. Pages whose
    background is already near white are returned as they are.
    """
    g = np.array(image.convert("L"))
    h, w = g.shape
    small = cv2.resize(g, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    bg_small = cv2.medianBlur(small, 25)
    if float(np.median(bg_small)) >= FLATTEN_BELOW:
        return image, False
    bg = cv2.resize(bg_small, (w, h), interpolation=cv2.INTER_LINEAR)
    flat = cv2.divide(g, np.maximum(bg, 1), scale=255)
    return Image.fromarray(flat).convert("RGB"), True


def prepare_for_ocr(image: Image.Image) -> tuple[Image.Image, PrepInfo]:
    """Erase ruling lines inside detected table regions and straighten the page when it is visibly tilted
    (by its ruling lines when it has a table, otherwise by its text lines). Straight pages without a ruled
    table come back as-is."""
    info = PrepInfo(size=image.size)
    try:
        regions = find_table_regions(image)
    except Exception as exc:                           # the detector must never fail a page read
        logger.warning("linefree_region_detect_failed", error=str(exc))
        regions = []
    gray = np.array(image.convert("L"))
    out = image
    skew = 0.0
    if regions:
        info.regions = len(regions)
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
    if not (MIN_SKEW_DEG <= abs(skew) <= MAX_SKEW_DEG):
        skew = 0.0
        try:
            angle, gain = estimate_text_skew(gray)
        except Exception as exc:
            logger.warning("text_skew_failed", error=str(exc)[:160])
            angle, gain = 0.0, 1.0
        if TEXT_SKEW_MIN_DEG <= abs(angle) <= TEXT_SKEW_MAX_DEG and gain >= TEXT_SKEW_MIN_GAIN:
            skew = angle
    if skew:
        out = out.rotate(skew, resample=Image.BICUBIC, fillcolor=(255, 255, 255))
        info.skew_deg = round(skew, 3)
        info.changed = True
    try:
        out, info.flattened = flatten_illumination(out)
    except Exception as exc:
        logger.warning("flatten_failed", error=str(exc)[:160])
    if info.flattened:
        info.changed = True
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
