"""
DOCINT — fast table-region finder (OpenCV, no model)
====================================================
Finds where ruled tables sit on a page by looking for long horizontal and
vertical lines. It takes a few milliseconds per page on CPU, so the table
step can crop to the table before running the (much slower) structure model
instead of paying for a layout-detection neural net on every page.

Pages without ruled tables return [] and the caller falls back to the whole
page, exactly as before.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

_MAX_SIDE = 1400        # analyse a downscaled copy; boxes are scaled back up
_MAX_REGIONS = 6


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
