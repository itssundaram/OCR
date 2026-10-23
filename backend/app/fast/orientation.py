"""
Page orientation (0/90/180/270) for scanned pages.

Why: about one scanned page in nine in the real corpus (appendix tables, nursing lists, a
rotated voucher side) is fed to the scanner sideways. Tesseract's layout analysis does not
turn those pages upright, so they are read as noise. The skew code in linefree.py only copes
with a few degrees.

Two cheap steps, both on a small copy of the page:
  1. projection test  - upright text has strongly banded row sums and flat column sums; sideways
                        text is the opposite (ratio of the two variances, rules removed).
  2. Tesseract OSD    - suggests 90 vs 270.
The suggestion is only a candidate. The reader OCRs the turned page too and keeps the turn only when it
yields clearly more confidently read words (measured on the real corpus: OSD alone was wrong or useless on
several pages, e.g. a table page that read 67 good words upright and 4 turned). 180-degree pages were not
seen in the corpus and are not handled.

  candidate_rotations(gray) -> Orientation   good_words(lines) / accept_rotation(before, after)
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

from app.core.logging import get_logger

logger = get_logger(__name__)

PROFILE_LONG_SIDE = 600     # the projection test runs on a copy this many px on its long side
SIDEWAYS_RATIO = 0.7        # row-variance / column-variance below this = text runs vertically
OSD_LONG_SIDE = 1600        # OSD input size (it needs readable glyphs, not the full scan)
OSD_MIN_CONF = 1.5          # Tesseract "Orientation confidence" accepted for a 90/270 turn
OSD_SURE_CONF = 8.0         # above this only the OSD direction is tried; below it the opposite turn is tried too
GOOD_WORD_CONF = 0.70       # Tesseract word confidence (0-1) that counts as "confidently read"
ACCEPT_GAIN = 1.3           # a turned page must yield 30 % more confident words ...
ACCEPT_MARGIN = 5           # ... plus this many, so noise on near-empty pages cannot flip it


@dataclass
class Orientation:
    rotate_cw: int = 0        # degrees to turn the page clockwise to make the text upright
    ratio: float = 1.0
    osd_conf: float = 0.0
    reason: str = "upright"
    alt: list[int] | None = None   # further turns to try when the first one does not pay off

    @property
    def changed(self) -> bool:
        return self.rotate_cw != 0


def sideways_ratio(gray: np.ndarray) -> float:
    """Row-profile variance over column-profile variance after removing ruling lines (<1 = sideways text)."""
    h, w = gray.shape
    k = PROFILE_LONG_SIDE / max(h, w)
    small = cv2.resize(gray, (max(8, int(w * k)), max(8, int(h * k))), interpolation=cv2.INTER_AREA)
    b = cv2.adaptiveThreshold(small, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 21, 12)
    hk = cv2.morphologyEx(b, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1)))
    vk = cv2.morphologyEx(b, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 25)))
    t = cv2.subtract(b, cv2.bitwise_or(hk, vk))
    r = float(np.diff(t.sum(1).astype(float)).var())
    c = float(np.diff(t.sum(0).astype(float)).var())
    return r / max(c, 1.0)


def _osd(gray: np.ndarray) -> tuple[int, float] | None:
    """(rotate_cw, confidence) from Tesseract's orientation detector, or None when it cannot tell."""
    try:
        import pytesseract
    except Exception:
        return None
    h, w = gray.shape
    k = min(1.0, OSD_LONG_SIDE / max(h, w))
    img = Image.fromarray(gray if k >= 1.0 else cv2.resize(gray, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA))
    try:
        d = pytesseract.image_to_osd(img, config="--psm 0 -c min_characters_to_try=40",
                                     output_type=pytesseract.Output.DICT)
        return int(d.get("rotate", 0)) % 360, float(d.get("orientation_conf", 0.0))
    except Exception as exc:                      # "Too few characters", missing osd data, ...
        logger.debug("osd_failed", error=str(exc)[:120])
        return None


def candidate_rotations(gray: np.ndarray) -> Orientation:
    """Which clockwise turns are worth *trying* for this page. Never decides alone: the caller reads the
    turned page and keeps it only when it yields clearly more confident words (see accept_rotation).

    rotate_cw holds the first candidate (0 = nothing to try); `alt` lists the others."""
    ratio = sideways_ratio(gray)
    if ratio >= SIDEWAYS_RATIO:
        return Orientation(0, ratio, 0.0, "upright")
    osd = _osd(gray)
    if osd is not None and osd[0] in (90, 270) and osd[1] >= OSD_MIN_CONF:
        return Orientation(osd[0], ratio, osd[1], "sideways", alt=[270 if osd[0] == 90 else 90] if osd[1] < OSD_SURE_CONF else [])
    conf = osd[1] if osd else 0.0
    return Orientation(90, ratio, conf, "sideways_unresolved", alt=[270])


def good_words(lines) -> int:
    """Count of confidently read words (2+ chars incl. a letter/digit): robust against the junk a sideways
    page produces, which inflates raw word counts and means but never the confident ones."""
    n = 0
    for ln in lines:
        for w in ln.words:
            t = (w.text or "").strip()
            if len(t) >= 2 and (w.confidence or 0.0) >= GOOD_WORD_CONF and any(c.isalnum() for c in t):
                n += 1
    return n


def accept_rotation(good_before: int, good_after: int) -> bool:
    return good_after >= good_before * ACCEPT_GAIN + ACCEPT_MARGIN


def rotate_image(image: Image.Image, rotate_cw: int) -> Image.Image:
    """Turn a page clockwise by 0/90/180/270 degrees (lossless, size swaps for 90/270)."""
    if rotate_cw == 90:
        return image.transpose(Image.ROTATE_270)
    if rotate_cw == 180:
        return image.transpose(Image.ROTATE_180)
    if rotate_cw == 270:
        return image.transpose(Image.ROTATE_90)
    return image


def unrotate_xy(x: float, y: float, rotate_cw: int, orig_w: int, orig_h: int) -> tuple[float, float]:
    """Map a point of the rotated page back to the original page's pixels (orig_w x orig_h)."""
    if rotate_cw == 90:        # forward: (x, y) -> (H - y, x)
        return y, orig_h - x
    if rotate_cw == 180:       # forward: (x, y) -> (W - x, H - y)
        return orig_w - x, orig_h - y
    if rotate_cw == 270:       # forward: (x, y) -> (y, W - x)
        return orig_w - y, x
    return x, y
