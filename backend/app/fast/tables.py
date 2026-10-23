"""
Tables from geometry: the ruling lines give the grid, the page's own words
(native or the single OCR pass) fill the cells. No table model, no second OCR.

  1. find_table_regions (OpenCV, ~10 ms/page) says where ruled tables are
  2. inside each region, long horizontal / vertical lines become row / column
     boundaries (columns come from word gaps when there are no vertical rules)
  3. every word goes to the cell that contains its centre

Borderless tables are not found by line detection; with
FAST_TATR_FALLBACK_ENABLED a TATR pass on a crop can be tried (time-boxed).
"""
from __future__ import annotations

import bisect
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from statistics import median

import cv2
import numpy as np

from app.core.config import settings
from app.core.logging import get_logger
from app.fast.triage import words_to_lines
from app.fast.types import PageData
from app.ocr.table_regions import find_table_regions
from app.pipelines.base import TableResult

logger = get_logger(__name__)


def _line_positions(mask: np.ndarray, axis: int, min_len: float) -> list[float]:
    """Centres of the long lines in a binary line mask. axis 0 = horizontal lines (y), 1 = vertical (x)."""
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    pos = []
    for i in range(1, n):
        w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        length = w if axis == 0 else h
        if length >= min_len:
            pos.append(float(cents[i][1] if axis == 0 else cents[i][0]))
    return pos


def _merge_close(vals: list[float], gap: float) -> list[float]:
    vals = sorted(vals)
    out: list[list[float]] = []
    for v in vals:
        if out and v - out[-1][-1] <= gap:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


def _columns_from_spans(spans: list[tuple[float, float]], x0: float, x1: float, med_h: float) -> list[float]:
    spans = sorted(spans)
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a - merged[-1][1] <= 0.8 * med_h:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    cuts = [(merged[i][1] + merged[i + 1][0]) / 2 for i in range(len(merged) - 1)]
    return [x0] + cuts + [x1]


def _md(headers: list[str], rows: list[list[str]]) -> str:
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    md = f"| {' | '.join(esc(h) for h in headers)} |\n| {' | '.join(['---'] * len(headers))} |\n"
    for r in rows:
        md += f"| {' | '.join(esc(c) for c in r)} |\n"
    return md


_SEP = set("-/.,:;| ")


def _split_word(text: str, x1: float, x2: float, b: float) -> tuple[str, str] | None:
    """A word box that straddles a column rule is two cells glued together
    ("8/MS-23057F"): cut the text at the character nearest the rule, preferring a
    separator or a letter/digit change. None when there is no sensible cut."""
    n = len(text)
    if n < 3 or x2 <= x1:
        return None
    frac = (b - x1) / (x2 - x1)
    if not (0.12 < frac < 0.88):
        return None
    guess = int(round(frac * n))
    best = None
    for off in (0, -1, 1, -2, 2):
        i = guess + off
        if not (1 <= i <= n - 1):
            continue
        a, c = text[i - 1], text[i]
        if a in _SEP or c in _SEP:
            best = i if c not in _SEP else i + 1
            break
        if a.isdigit() != c.isdigit() and a.isalnum() and c.isalnum():
            best = i
            break
    if best is None or not (1 <= best <= n - 1):
        return None
    left, right = text[:best].strip(" -/|"), text[best:].strip(" -/|")
    return (left, right) if left and right else None


def _boundaries(lines: list[float], extent: float, words_spans: list[tuple[float, float]], strip_limit: float | None) -> list[float]:
    """Row/column boundaries from the detected rules. With enough rules only
    they are used (text outside the first/last rule is title or margin, not
    table); an open edge is closed at the region edge only when a strip that
    fits one cell holds words."""
    lines = _merge_close(sorted(lines), 5)
    b = list(lines)
    if strip_limit is not None and b:
        if 0 < b[0] <= strip_limit and any(0 <= (a + c) / 2 < b[0] for a, c in words_spans):
            b = [0.0] + b
        if 0 < extent - b[-1] <= strip_limit and any((a + c) / 2 > b[-1] for a, c in words_spans):
            b = b + [extent]
    return b


def _grid_table(page: PageData, region: tuple[int, int, int, int], index: int) -> TableResult | None:
    x1, y1, x2, y2 = region
    rw, rh = x2 - x1, y2 - y1
    if rw < 60 or rh < 40 or page.image is None:
        return None
    gray = np.array(page.image.crop(region).convert("L"))
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 10)
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, rw // 15), 1)))
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, rh // 15))))

    ys = _merge_close(_line_positions(horiz, 0, 0.4 * rw), 5)
    xs = _merge_close(_line_positions(vert, 1, 0.4 * rh), 5)

    words = [w for w in page.words()
             if w.bbox and x1 <= (w.bbox.x1 + w.bbox.x2) / 2 <= x2 and y1 <= (w.bbox.y1 + w.bbox.y2) / 2 <= y2]
    if len(words) < 2:
        return None
    med_h = median(max(1.0, w.bbox.y2 - w.bbox.y1) for w in words)

    outside: list = []
    if len(ys) >= 3:
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        row_b = _boundaries(ys, float(rh), [(w.bbox.y1 - y1, w.bbox.y2 - y1) for w in words], 2.2 * median(gaps))
    else:
        row_b = _merge_close(sorted(set([0.0, float(rh)] + ys)), 5)
    if len(xs) >= 3:
        col_b = _merge_close(sorted(xs), 5)               # rules only: margins hold no cells
    elif len(xs) == 2:
        col_b = _merge_close(sorted(set([0.0, float(rw)] + xs)), 5)
    else:
        col_b = _columns_from_spans([(w.bbox.x1 - x1, w.bbox.x2 - x1) for w in words], 0.0, float(rw), med_h)
    n_rows, n_cols = len(row_b) - 1, len(col_b) - 1
    if n_rows < 2 or n_cols < 2 or n_rows > 300 or n_cols > 40:
        return None

    # (text, x1, y1, x2, y2, conf) pieces, so a straddling word can become two
    pieces: list[tuple[str, float, float, float, float, float | None]] = []
    for w in words:
        b = w.bbox
        done = False
        for xb in col_b[1:-1]:
            if b.x1 - x1 < xb < b.x2 - x1:
                cut = _split_word(w.text, b.x1 - x1, b.x2 - x1, xb)
                if cut:
                    fr = (xb - (b.x1 - x1)) / max(1.0, b.x2 - b.x1)
                    mid = b.x1 + fr * (b.x2 - b.x1)
                    pieces.append((cut[0], b.x1, b.y1, mid, b.y2, None))      # glued word: confidence unknown per part
                    pieces.append((cut[1], mid, b.y1, b.x2, b.y2, None))
                    done = True
                    break
        if not done:
            pieces.append((w.text, b.x1, b.y1, b.x2, b.y2, w.confidence))

    cell_words: dict[tuple[int, int], list] = {}
    for text, bx1, by1, bx2, by2, conf in pieces:
        cx, cy = (bx1 + bx2) / 2 - x1, (by1 + by2) / 2 - y1
        if cy < row_b[0] or cy > row_b[-1] or cx < col_b[0] or cx > col_b[-1]:
            outside.append((by1, bx1, text))
            continue
        r = min(n_rows - 1, max(0, bisect.bisect_right(row_b, cy) - 1))
        c = min(n_cols - 1, max(0, bisect.bisect_right(col_b, cx) - 1))
        cell_words.setdefault((r, c), []).append((bx1, by1, bx2, by2, text, conf))

    grid = [[""] * n_cols for _ in range(n_rows)]
    confs: dict[tuple[int, int], float | None] = {}
    for (r, c), ws in cell_words.items():
        lines = words_to_lines([(a, b, cc, d, t) for a, b, cc, d, t, _ in ws])
        grid[r][c] = " ".join(ln.text for ln in lines)
        cs = [cf for *_, cf in ws if cf is not None]
        confs[(r, c)] = round(sum(cs) / len(cs), 3) if cs else None

    keep_rows = [r for r in range(n_rows) if any(grid[r])]
    keep_cols = [c for c in range(n_cols) if any(grid[r][c] for r in range(n_rows))]
    if len(keep_rows) < 2 or len(keep_cols) < 2:
        return None

    cells = []
    for ri, r in enumerate(keep_rows):
        for ci, c in enumerate(keep_cols):
            cells.append({
                "row": ri, "col": ci, "text": grid[r][c],
                "bbox": {"x1": int(x1 + col_b[c]), "y1": int(y1 + row_b[r]),
                         "x2": int(x1 + col_b[c + 1]), "y2": int(y1 + row_b[r + 1])},
                "is_header": ri == 0,
                "confidence": confs.get((r, c)),
                "page": page.page_number,
            })
    table_rows = [[grid[r][c] for c in keep_cols] for r in keep_rows]
    headers, body = table_rows[0], table_rows[1:]
    native = page.source == "native"
    outside.sort()
    return TableResult(
        table_index=index, page_number=page.page_number,
        headers=headers, rows=body,
        confidence=0.92 if native else 0.85,
        bbox={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
        cells=cells, markdown=_md(headers, body),
        extraction_method="grid_lines",
        meta={"col_edges": [round(x1 + col_b[c]) for c in keep_cols] + [round(x1 + col_b[keep_cols[-1] + 1])],
              "outside_text": " ".join(t for *_, t in outside)[:500]},
    )


def _tatr_crop(page: PageData, region: tuple[int, int, int, int], index: int) -> TableResult | None:
    from app.core.model_registry import get_tatr_engine
    crop = page.image.crop(region)
    read = get_tatr_engine().extract_tables(crop)
    if not read:
        return None
    t = read[0]
    ox, oy = region[0], region[1]
    cells = []
    for c in getattr(t, "cells", None) or []:
        c = dict(c)
        b = c.get("bbox")
        if b:
            c["bbox"] = {"x1": b["x1"] + ox, "y1": b["y1"] + oy, "x2": b["x2"] + ox, "y2": b["y2"] + oy}
        cells.append(c)
    return TableResult(
        table_index=index, page_number=page.page_number, headers=t.headers, rows=t.rows,
        confidence=0.85, bbox={"x1": region[0], "y1": region[1], "x2": region[2], "y2": region[3]},
        cells=cells, markdown=t.markdown, extraction_method="tatr",
    )


def extract_page_tables(page: PageData, first_index: int = 1) -> list[TableResult]:
    """All tables on one page. Pages without a ruled table cost one line scan."""
    if page.image is None:
        return []
    out: list[TableResult] = []
    for region in find_table_regions(page.image):
        t = _grid_table(page, region, first_index + len(out))
        if t is None and settings.FAST_TATR_FALLBACK_ENABLED:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_tatr_crop, page, region, first_index + len(out))
                try:
                    t = fut.result(timeout=settings.FAST_TABLE_TIMEOUT_S)
                except FutureTimeout:
                    logger.warning("fast_tatr_timeout", page=page.page_number)
                except Exception as exc:
                    logger.warning("fast_tatr_failed", page=page.page_number, error=str(exc))
        if t is not None:
            out.append(t)
    return out
