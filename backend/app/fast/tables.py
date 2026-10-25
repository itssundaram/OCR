"""
Tables from geometry: the ruling lines give the grid, the page's own words
(native or the single OCR pass) fill the cells. No table model, no second OCR.

  1. find_table_regions (OpenCV, ~10 ms/page) says where ruled tables are
  2. inside each region, long horizontal / vertical lines become row / column
     boundaries (columns come from word gaps when there are no vertical rules)
  3. every word goes to the cell that contains its centre

A table with a header rule and a closing rule but no row rules (an invoice) is read with rows taken from the text
lines. Tables with no rules at all are not found by line detection; with
FAST_TATR_FALLBACK_ENABLED a TATR pass on a crop can be tried (time-boxed).
"""
from __future__ import annotations

import bisect
import re
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

TEXT_ROWS_MIN_COLS = 3    # a grid-less table read by text lines needs at least this many columns ...
TEXT_ROWS_MIN_ROWS = 4    # ... and a header plus this many rows - smaller blocks are forms, not tables


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


def _h_slope(mask: np.ndarray, min_len: float) -> float:
    """Median tilt (dy/dx) of the long horizontal rules: a scan that is a degree or two off makes the right-hand
    columns sit higher than the left-hand ones, which merges a header row with the first data row."""
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    slopes: list[float] = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_WIDTH] < min_len:
            continue
        pts = cv2.findNonZero((lab == i).astype(np.uint8))
        if pts is None or len(pts) < 20:
            continue
        vx, vy = cv2.fitLine(pts, cv2.DIST_L2, 0, 0.01, 0.01)[:2].ravel()
        if abs(vx) > 1e-6:
            slopes.append(float(vy / vx))
    if len(slopes) < 2:
        return 0.0
    m = float(median(slopes))
    return m if 0.003 <= abs(m) <= 0.08 else 0.0


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


def _split_header_row(row_b: list[float], ri: int, col_b: list[float], words: list, x1: float, y1: float, med_h: float,
                      slope: float = 0.0, mid_x: float = 0.0) -> list[float] | None:
    """Faint or missing rule between the header and the first data row => both sit in ONE rule row.

    Splits the first row in two when it holds (at least) two aligned text lines across several columns, the first
    line is short text (a header) and the second line is data (digits) in most of the same columns. Row wrapping
    inside ONE column (a cell that runs onto a second line) is never split. No vocabulary involved.
    """
    if len(row_b) < 3 or len(col_b) < 3:
        return row_b
    top, bottom = row_b[ri], row_b[ri + 1]
    inside = []
    for w in words:
        cx = (w.bbox.x1 + w.bbox.x2) / 2 - x1
        cy = (w.bbox.y1 + w.bbox.y2) / 2 - y1 - slope * (cx - mid_x)          # straightened: see _h_slope
        if top <= cy < bottom and col_b[0] <= cx <= col_b[-1] and any(ch.isalnum() for ch in w.text):
            inside.append((cy, cx, w))                     # box borders read as "|" or "_" are not words
    if len(inside) < 4:
        return None                                    # a strip above the table: look at the next rule row
    inside.sort(key=lambda t: t[0])
    lines: list[list] = [[inside[0]]]
    for item in inside[1:]:
        cur = lines[-1]
        if item[0] - sum(t[0] for t in cur) / len(cur) > 0.75 * med_h:
            lines.append([item])
        else:
            cur.append(item)
    if len(lines) < 2:
        return row_b

    def cols_of(line) -> set[int]:
        return {min(len(col_b) - 2, max(0, bisect.bisect_right(col_b, t[1]) - 1)) for t in line}

    def text_of(line) -> list[str]:
        out: dict[int, list[str]] = {}
        for t in sorted(line, key=lambda t: t[1]):
            c = min(len(col_b) - 2, max(0, bisect.bisect_right(col_b, t[1]) - 1))
            out.setdefault(c, []).append(t[2].text)
        return [" ".join(v) for v in out.values()]

    def alpha_share(cells: list[str]) -> float:
        good = sum(1 for c in cells if sum(ch.isalpha() for ch in c) >= 0.7 * max(1, sum(ch.isalnum() for ch in c)))
        return good / max(1, len(cells))

    def digit_share(cells: list[str]) -> float:
        return sum(1 for c in cells if any(ch.isdigit() for ch in c)) / max(1, len(cells))

    # A header may itself be two or three printed lines ("Cos" over "Section", "Demand" over "Quantity"). Find the FIRST
    # line that is data (digits in several of the header's columns) with only header-like lines above it, and cut there.
    for k in range(1, min(len(lines), 5)):
        head = [t for ln in lines[:k] for t in ln]
        data = lines[k]
        hc, dc = cols_of(head), cols_of(data)
        if len(hc) < 2 or len(dc) < 2 or len(hc & dc) < max(2, int(0.6 * min(len(hc), len(dc)))):
            continue                                   # not aligned across columns: a wrapped cell, keep as one row
        th, td = text_of(head), text_of(data)
        if alpha_share(th) < 0.8 or digit_share(th) > 0.2 or digit_share(td) < 0.3:
            continue                                   # lines above are not all "words", or this line is not data
        cut = (max(t[0] + (t[2].bbox.y2 - t[2].bbox.y1) / 2 for ln in lines[:k] for t in ln)
               + min(t[0] - (t[2].bbox.y2 - t[2].bbox.y1) / 2 for t in data)) / 2
        if not (top + 2 < cut < bottom - 2):
            return row_b
        return sorted([*row_b, cut])
    return row_b


def _split_merged_header(row_b: list[float], col_b: list[float], words: list, x1: float, y1: float, med_h: float,
                         slope: float = 0.0, mid_x: float = 0.0) -> list[float]:
    """The header row is the first rule row that holds text (a strip of margin above it is skipped)."""
    if len(row_b) < 3 or len(col_b) < 3:
        return row_b
    for ri in range(min(3, len(row_b) - 1)):
        got = _split_header_row(row_b, ri, col_b, words, x1, y1, med_h, slope, mid_x)
        if got is not None:
            return got
    return row_b


def _rows_from_text(words: list, y1: float, rh: float, med_h: float) -> list[float]:
    """Row boundaries for a table with no row rules: the page's own text lines. A line sitting closer to the one above
    than the table's usual line pitch is a wrapped continuation of that row, not a new row."""
    ctr = sorted((((w.bbox.y1 + w.bbox.y2) / 2 - y1), w.bbox.y1 - y1, w.bbox.y2 - y1) for w in words)
    lines: list[list[float]] = []                      # [mean centre, top, bottom]
    for cy, top, bot in ctr:
        if lines and cy - lines[-1][0] <= 0.6 * med_h:
            n = lines[-1]
            n[0] = (n[0] + cy) / 2
            n[1], n[2] = min(n[1], top), max(n[2], bot)
        else:
            lines.append([cy, top, bot])
    if len(lines) < 2:
        return []
    gaps = [b[0] - a[0] for a, b in zip(lines, lines[1:])]
    pitch = median(gaps)
    starts = [0]
    for i, g in enumerate(gaps, start=1):
        if g >= 0.75 * pitch:
            starts.append(i)
    edges = [0.0]
    for k in range(1, len(starts)):
        prev_last, nxt_first = lines[starts[k] - 1], lines[starts[k]]
        edges.append((prev_last[2] + nxt_first[1]) / 2)
    edges.append(rh)
    return edges


def _grid_table(page: PageData, region: tuple[int, int, int, int], index: int) -> TableResult | None:
    x1, y1, x2, y2 = region
    rw, rh = x2 - x1, y2 - y1
    if rw < 60 or rh < 40 or page.image is None:
        return None
    gray = np.array(page.image.crop(region).convert("L"))
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 10)
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, rw // 15), 1)))
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, rh // 15))))

    slope = _h_slope(horiz, 0.4 * rw)
    ys = _merge_close(_line_positions(horiz, 0, 0.4 * rw), 5)
    xs = _merge_close(_line_positions(vert, 1, 0.4 * rh), 5)

    words = [w for w in page.words()
             if w.bbox and x1 <= (w.bbox.x1 + w.bbox.x2) / 2 <= x2 and y1 <= (w.bbox.y1 + w.bbox.y2) / 2 <= y2]
    if len(words) < 2:
        return None
    med_h = median(max(1.0, w.bbox.y2 - w.bbox.y1) for w in words)

    outside: list = []
    text_rows: list[float] = []
    if len(ys) >= 3:
        gaps = [b - a for a, b in zip(ys, ys[1:])]
        row_b = _boundaries(ys, float(rh), [(w.bbox.y1 - y1, w.bbox.y2 - y1) for w in words], 2.2 * median(gaps))
    else:
        row_b = _merge_close(sorted(set([0.0, float(rh)] + ys)), 5)
        rule_row_b = row_b
        text_rows = _rows_from_text(words, y1, float(rh), med_h) if (len(words) >= 6 and len(xs) < 2) else []
        if text_rows:
            row_b = text_rows                                          # no grid at all: rows are the text lines
    if len(xs) >= 3:
        col_b = _merge_close(sorted(xs), 5)               # rules only: margins hold no cells
    elif len(xs) == 2:
        col_b = _merge_close(sorted(set([0.0, float(rw)] + xs)), 5)
    else:
        col_b = _columns_from_spans([(w.bbox.x1 - x1, w.bbox.x2 - x1) for w in words], 0.0, float(rw), med_h)
    if text_rows and (len(col_b) - 1 < TEXT_ROWS_MIN_COLS or len(row_b) - 1 < TEXT_ROWS_MIN_ROWS):
        row_b = rule_row_b                                             # too small to be a table: a form block, as before
    row_b = _split_merged_header(row_b, col_b, words, x1, y1, med_h, slope, rw / 2)
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
        cy -= slope * (cx - rw / 2)                       # row of the word on a tilted scan
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

    real = lambda t: bool(re.search(r"[A-Za-z0-9]", t or ""))        # noqa: E731  (box borders read as "|" or "_" are not text)
    keep_rows = [r for r in range(n_rows) if any(real(c) for c in grid[r])]
    keep_cols = [c for c in range(n_cols) if any(real(grid[r][c]) for r in range(n_rows))]
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


def looks_like_form_block(t: TableResult) -> bool:
    """A boxed form ("Make: ... Date of inspection: ...") is drawn with rules like a table but is not one: few
    columns, and every cell is a paragraph of "Label : value" lines. Those pairs are read by key:value
    discovery already, so the block is not returned as a data table."""
    cells = [c.get("text") or "" for c in t.cells if not c.get("is_header")] or [c for r in t.rows for c in r]
    cells = [c for c in cells if c.strip()]
    if len(t.headers) > 3 or len(cells) < 2:
        return False
    avg_len = sum(len(c) for c in cells) / len(cells)
    label_runs = sum(len(re.findall(r"[A-Za-z][A-Za-z ./&()']{2,30}\s*:\s", c)) for c in cells) / len(cells)
    if avg_len > 90 and label_runs >= 3:
        return True
    # a checklist laid out as 2-3 columns of "Label : value" cells
    colon_share = sum(1 for c in cells if re.search(r"\w\s*:\s*\S", c)) / len(cells)
    return len(cells) >= 4 and colon_share >= 0.6


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
        if t is not None and looks_like_form_block(t):
            logger.info("fast_table_skipped_form_block", page=page.page_number, cols=len(t.headers))
            t = None
        if t is not None:
            out.append(t)
    return out
