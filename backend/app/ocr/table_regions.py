"""
DOCINT — fast table-region finder (OpenCV, no model)
====================================================
Finds where ruled tables sit on a page by looking for long horizontal and
vertical lines. It takes a few milliseconds per page on CPU, so the table
step can crop to the table before running the (much slower) structure model
instead of paying for a layout-detection neural net on every page.

Pages without ruled tables return [] and the caller falls back to the whole
page, exactly as before.

A second, deliberately conservative rule catches the very common invoice / statement layout that has NO grid:
a header rule and a closing rule of the same width with several rows of text laid out in columns between them.
It runs only when the grid rule found nothing on the page, and it needs real column gutters, so a paragraph or a
signature line between two rules is not mistaken for a table.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

_MAX_SIDE = 1400        # analyse a downscaled copy; boxes are scaled back up
_MAX_REGIONS = 6
_FRAME_BAND = 0.04       # a long line this close to the page edge is the scan's own border / shadow, not a table rule
_RULE_MIN_WIDTH = 0.40  # a table rule spans at least this share of the page width
_RULE_PAIR_ALIGN = 0.04  # the two rules start and end within this share of the page width of each other
_PAIR_MIN_GAP = 0.05     # share of page height between the rules (room for a header and a few rows)
_PAIR_MIN_ROWS = 4       # text rows between the rules
_PAIR_MIN_GUTTERS = 2    # empty vertical gutters inside the block => at least three columns


def _merge(boxes: list[list[int]], gap: int) -> list[list[int]]:
    """Union boxes that overlap or sit within `gap` px of each other."""
    boxes = [b[:] for b in boxes]
    changed = True
    while changed:
        changed = False
        out: list[list[int]] = []
        for b in boxes:
            for o in out:
                if b[0] <= o[2] + gap and o[0] <= b[2] + gap and b[1] <= o[3] + gap and o[1] <= b[3] + gap:
                    o[0], o[1], o[2], o[3] = min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def drop_page_frame(mask: np.ndarray, horizontal: bool) -> np.ndarray:
    """Remove the scan's own edge from a line mask: page borders, scanner shadows and the paper edge show up as very
    long lines hugging the image boundary. Left in, they join every real rule into one page-sized "table" (a ruled
    table on a scanned page then reads as one cell). A table rule is never both long AND within a few percent of the
    boundary, so only such lines go; everything else is untouched."""
    h, w = mask.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = mask.copy()
    for i in range(1, n):
        x, y, bw, bh = (int(v) for v in stats[i][:4])
        if horizontal:
            long_enough, near_edge, whole = bw >= 0.5 * w, y <= _FRAME_BAND * h or y + bh >= h - _FRAME_BAND * h, bw >= 0.97 * w
        else:
            long_enough, near_edge, whole = bh >= 0.5 * h, x <= _FRAME_BAND * w or x + bw >= w - _FRAME_BAND * w, bh >= 0.97 * h
        if (long_enough and near_edge) or whole:
            out[labels == i] = 0
    return out


def _trim_to_grid(vert: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """A ruled form box sitting directly above a table joins it into one region. The table has several vertical
    rules running through its rows; the box has only its two sides. Rows with fewer than four vertical rules at the
    top or bottom of a region that clearly contains a real grid are cut off (only when that is a sizeable part)."""
    x1, y1, x2, y2 = box
    sub = vert[y1:y2, x1:x2]
    n, lab, stats, _ = cv2.connectedComponentsWithStats(sub, connectivity=8)
    cover = np.zeros(sub.shape[0], dtype=np.int32)
    for i in range(1, n):
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if bh >= 0.08 * sub.shape[0] and bh >= 25:
            ya = int(stats[i, cv2.CC_STAT_TOP])
            cover[ya:ya + bh] += 1
    rows = np.flatnonzero(cover >= 4)
    if rows.size == 0:
        return box
    top, bot = int(rows[0]), int(rows[-1])
    height = sub.shape[0]
    if (bot - top) < 0.15 * height or ((top + (height - bot)) < 0.2 * height):
        return box
    pad = max(4, int(0.004 * height))
    return (x1, y1 + max(0, top - pad), x2, y1 + min(height, bot + pad))


def _long_rules(horiz: np.ndarray, w: int, h: int) -> list[tuple[int, int, int]]:
    """Thin, long horizontal rules as (y_centre, x1, x2), top to bottom; double rules are merged."""
    contours, _ = cv2.findContours(horiz, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rules: list[tuple[int, int, int]] = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw >= _RULE_MIN_WIDTH * w and bh <= max(6, 0.012 * h):
            rules.append((y + bh // 2, x, x + bw))
    rules.sort()
    merged: list[tuple[int, int, int]] = []
    for r in rules:
        if merged and r[0] - merged[-1][0] <= 0.006 * h:
            m = merged[-1]
            merged[-1] = (m[0], min(m[1], r[1]), max(m[2], r[2]))
        else:
            merged.append(r)
    return merged


def _text_bands(ink_rows: np.ndarray, max_gap: int) -> list[tuple[int, int]]:
    """Runs of rows that hold ink (a gap of up to `max_gap` empty rows stays inside one band)."""
    bands: list[list[int]] = []
    for y in np.flatnonzero(ink_rows):
        if bands and y - bands[-1][1] <= max_gap + 1:
            bands[-1][1] = int(y)
        else:
            bands.append([int(y), int(y)])
    return [(a, b) for a, b in bands]


def _gutters(ink_cols: np.ndarray, min_width: int) -> int:
    idx = np.flatnonzero(ink_cols)
    if idx.size < 2:
        return 0
    gaps = np.diff(idx) - 1
    return int(np.count_nonzero(gaps >= min_width))


def _rule_pair_regions(binary: np.ndarray, horiz: np.ndarray, vert: np.ndarray, w: int, h: int) -> list[list[int]]:
    """Tables with no grid: a header rule and a closing rule enclosing rows of text set in columns."""
    ink = cv2.bitwise_and(binary, cv2.bitwise_not(cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8))))
    rules = _long_rules(horiz, w, h)
    out: list[list[int]] = []
    i = 0
    while i < len(rules) - 1:
        (ya, xa1, xa2), (yb, xb1, xb2) = rules[i], rules[i + 1]
        aligned = abs(xa1 - xb1) <= _RULE_PAIR_ALIGN * w and abs(xa2 - xb2) <= _RULE_PAIR_ALIGN * w
        if not aligned or (yb - ya) < _PAIR_MIN_GAP * h:
            i += 1
            continue
        x1, x2 = min(xa1, xb1), max(xa2, xb2)
        block = ink[ya + 3:yb - 2, x1:x2]
        bands = _text_bands(block.any(axis=1), max_gap=max(1, int(0.003 * h)))
        if len(bands) < _PAIR_MIN_ROWS or _gutters(block.any(axis=0), max(4, int(0.012 * w))) < _PAIR_MIN_GUTTERS:
            i += 1
            continue
        # the header line usually sits just above the first rule: include it
        top = ya
        look = max(0, ya - int(0.045 * h))
        above = _text_bands(ink[look:ya - 1, x1:x2].any(axis=1), max_gap=max(1, int(0.003 * h)))
        if above and (ya - 1 - (look + above[-1][1])) <= 0.015 * h:
            top = look + above[-1][0]
        out.append([x1, top, x2, yb])
        i += 2
    return out


def find_table_regions(page: Image.Image, pad: int = 10) -> list[tuple[int, int, int, int]]:
    """Return table boxes (x1, y1, x2, y2) in page pixels, top to bottom."""
    w0, h0 = page.size
    if w0 < 50 or h0 < 50:
        return []
    scale = min(1.0, _MAX_SIDE / max(w0, h0))
    gray = np.array(page.convert("L"))
    if scale < 1.0:
        gray = cv2.resize(gray, (int(w0 * scale), int(h0 * scale)), interpolation=cv2.INTER_AREA)
    h, w = gray.shape

    def masks(g: np.ndarray):
        b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 10)
        hz = cv2.morphologyEx(b, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, w // 20), 1)))
        vt = cv2.morphologyEx(b, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, h // 40))))
        return b, hz, vt

    binary, horiz, vert = masks(gray)

    # A crooked scan: the bounding boxes of two tilted tables (or a form box above a table) overlap and merge into one
    # region. Look at the page straightened, then map the boxes back to the page as scanned.
    back = None
    try:
        from app.fast.linefree import MIN_SKEW_DEG, estimate_skew
        angle = estimate_skew(gray, horiz)
        if abs(angle) >= MIN_SKEW_DEG:
            rot = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            gray = cv2.warpAffine(gray, rot, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
            binary, horiz, vert = masks(gray)
            back = cv2.invertAffineTransform(rot)
    except Exception:
        back = None

    horiz, vert = drop_page_frame(horiz, True), drop_page_frame(vert, False)
    joined = cv2.dilate(cv2.bitwise_or(horiz, vert), cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), iterations=2)
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes: list[list[int]] = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw >= 0.15 * w and bh >= 0.025 * h:
            boxes.append([x, y, x + bw, y + bh])
    boxes = _merge(boxes, gap=int(0.005 * h))

    regions: list[tuple[int, int, int, int]] = []
    for x1, y1, x2, y2 in boxes:
        bw, bh = x2 - x1, y2 - y1
        h_lines = int(np.count_nonzero(horiz[y1:y2, x1:x2])) / max(1, bw)
        v_lines = int(np.count_nonzero(vert[y1:y2, x1:x2])) / max(1, bh)
        # a ruled table: several horizontal rules, or rules in both directions
        if h_lines >= 3 or (h_lines >= 2 and v_lines >= 2):
            regions.append((x1, y1, x2, y2))

    if not regions:
        regions = [tuple(b) for b in _rule_pair_regions(binary, horiz, vert, w, h)]
    else:
        regions = [_trim_to_grid(vert, r) for r in regions]

    regions.sort(key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), reverse=True)
    regions = regions[:_MAX_REGIONS]
    regions.sort(key=lambda r: r[1])

    if back is not None:                                  # boxes found on the straightened page -> page as scanned
        mapped = []
        for x1, y1, x2, y2 in regions:
            pts = [(back[0, 0] * x + back[0, 1] * y + back[0, 2], back[1, 0] * x + back[1, 1] * y + back[1, 2])
                   for x, y in ((x1, y1), (x2, y1), (x1, y2), (x2, y2))]
            mapped.append((int(max(0, min(p[0] for p in pts))), int(max(0, min(p[1] for p in pts))),
                           int(min(w, max(p[0] for p in pts))), int(min(h, max(p[1] for p in pts)))))
        regions = mapped

    out = []
    for x1, y1, x2, y2 in regions:
        out.append((
            max(0, int(x1 / scale) - pad), max(0, int(y1 / scale) - pad),
            min(w0, int(x2 / scale) + pad), min(h0, int(y2 / scale) + pad),
        ))
    return out
