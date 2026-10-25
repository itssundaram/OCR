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

    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 10)
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, w // 20), 1)))
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, h // 40))))

    joined = cv2.dilate(cv2.bitwise_or(horiz, vert), cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), iterations=2)
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes: list[list[int]] = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw >= 0.15 * w and bh >= 0.025 * h:
            boxes.append([x, y, x + bw, y + bh])
    boxes = _merge(boxes, gap=int(0.01 * h))

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

    regions.sort(key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), reverse=True)
    regions = regions[:_MAX_REGIONS]
    regions.sort(key=lambda r: r[1])

    out = []
    for x1, y1, x2, y2 in regions:
        out.append((
            max(0, int(x1 / scale) - pad), max(0, int(y1 / scale) - pad),
            min(w0, int(x2 / scale) + pad), min(h0, int(y2 / scale) + pad),
        ))
    return out
