"""
Structure: words/lines -> reading order -> paragraphs and headings.

Pure geometry on the words every page already has (native or OCR); no model.
Two-column pages are detected from a vertical gutter that (almost) no line
crosses; everything else is read top to bottom.
"""
from __future__ import annotations

import re
from statistics import median

from app.fast.types import PageData
from app.ocr.base import OCRLine

_LIST_RE = re.compile(r"^\s*(\(?\d{1,3}[.)]|\(?[a-z][.)]|[-•*·])\s+", re.I)


def _h(ln: OCRLine) -> float:
    return max(1.0, ln.bbox.y2 - ln.bbox.y1)


def _gutter(lines: list[OCRLine], width: int) -> float | None:
    n = len(lines)
    if n < 8 or width <= 0:
        return None
    step = max(1, width // 200)
    best_x, best_cross = None, None
    for x in range(int(width * 0.3), int(width * 0.7), step):
        crossing = sum(1 for ln in lines if ln.bbox.x1 < x < ln.bbox.x2)
        if best_cross is None or crossing < best_cross:
            best_x, best_cross = x, crossing
    if best_x is None or best_cross > 0.1 * n:
        return None
    left = sum(1 for ln in lines if (ln.bbox.x1 + ln.bbox.x2) / 2 < best_x)
    if left < 0.25 * n or (n - left) < 0.25 * n:
        return None
    return float(best_x)


def reading_order(lines: list[OCRLine], width: int) -> list[OCRLine]:
    lines = [ln for ln in lines if ln.bbox is not None and ln.text.strip()]
    g = _gutter(lines, width)
    if g is None:
        return sorted(lines, key=lambda ln: (round(ln.bbox.y1 / 6), ln.bbox.x1))
    left = [ln for ln in lines if (ln.bbox.x1 + ln.bbox.x2) / 2 < g]
    right = [ln for ln in lines if (ln.bbox.x1 + ln.bbox.x2) / 2 >= g]
    key = lambda ln: (ln.bbox.y1, ln.bbox.x1)  # noqa: E731
    return sorted(left, key=key) + sorted(right, key=key)


def build_blocks(page: PageData) -> list[dict]:
    """Paragraph / heading / list_item blocks in reading order, with page-pixel boxes."""
    ordered = reading_order(page.lines, page.width)
    if not ordered:
        return []
    med = median(_h(ln) for ln in ordered)
    blocks: list[list[OCRLine]] = []
    cur: list[OCRLine] = []
    for ln in ordered:
        if cur:
            prev = cur[-1]
            gap = ln.bbox.y1 - prev.bbox.y2
            back_up = ln.bbox.y1 < prev.bbox.y1 - med          # jumped to the next column
            far_left = abs(ln.bbox.x1 - cur[0].bbox.x1) > 3 * med
            if gap > 0.9 * med or back_up or far_left or _LIST_RE.match(ln.text):
                blocks.append(cur)
                cur = []
        cur.append(ln)
    if cur:
        blocks.append(cur)

    out = []
    for grp in blocks:
        text = " ".join(ln.text.strip() for ln in grp)
        x1 = min(ln.bbox.x1 for ln in grp)
        y1 = min(ln.bbox.y1 for ln in grp)
        x2 = max(ln.bbox.x2 for ln in grp)
        y2 = max(ln.bbox.y2 for ln in grp)
        if len(grp) == 1 and (_h(grp[0]) >= 1.3 * med or (text.isupper() and len(text.split()) <= 8)):
            kind = "heading"
        elif _LIST_RE.match(grp[0].text):
            kind = "list_item"
        else:
            kind = "paragraph"
        out.append({
            "type": kind, "page": page.page_number, "text": text, "lines": len(grp),
            "bbox": {"x1": round(x1, 1), "y1": round(y1, 1), "x2": round(x2, 1), "y2": round(y2, 1)},
        })
    return out
