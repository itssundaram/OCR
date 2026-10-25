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
from app.fast.tableschema import looks_like_noise_table
from app.fast.triage import words_to_lines
from app.fast.types import PageData
from app.ocr.table_regions import find_table_regions
from app.pipelines.base import TableResult

logger = get_logger(__name__)

_MAX_TEXT_TABLES = 3
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


def _straighten(gray: np.ndarray, horiz: np.ndarray) -> tuple[np.ndarray | None, np.ndarray | None]:
    """A crooked scan breaks the row rules into stair-steps (rows merge, the header slides). Estimate the tilt of the long
    rules; when it is real, return (matrix, matrix_inverse) of the counter-clockwise turn that makes them level."""
    from app.fast.linefree import MIN_SKEW_DEG, estimate_skew
    try:
        angle = estimate_skew(gray, horiz)
    except Exception:
        return None, None
    if abs(angle) < MIN_SKEW_DEG:
        return None, None
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return m, cv2.invertAffineTransform(m)


def _apply(m: np.ndarray, x: float, y: float) -> tuple[float, float]:
    return m[0, 0] * x + m[0, 1] * y + m[0, 2], m[1, 0] * x + m[1, 1] * y + m[1, 2]


def _hull(m: np.ndarray, ax1: float, ay1: float, ax2: float, ay2: float) -> tuple[float, float, float, float]:
    pts = [_apply(m, x, y) for x, y in ((ax1, ay1), (ax2, ay1), (ax1, ay2), (ax2, ay2))]
    xs_, ys_ = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs_), min(ys_), max(xs_), max(ys_)


def _split_tall_rows(row_b: list[float], col_b: list[float], words: list, x1: float, y1: float) -> list[float]:
    """A faint or broken rule can leave two rows in one grid row. A body row at least ~1.9x taller than its neighbours
    that holds a clean horizontal gap (no word crosses it) near its middle, with text on both sides of the gap in at
    least two columns, is cut there. Wrapped cells alone (one tall column) never trigger it."""
    if len(row_b) < 4:
        return row_b
    heights = [b - a for a, b in zip(row_b[1:], row_b[2:])]
    med = median(heights) if heights else 0.0
    if med <= 0:
        return row_b
    out = list(row_b)
    for r in range(1, len(row_b) - 2):                     # never the last row: the free space under a table holds specks
        top, bot = row_b[r], row_b[r + 1]
        if bot - top < 1.9 * med:
            continue
        inside = sorted((w for w in words if top <= (w.bbox.y1 + w.bbox.y2) / 2 - y1 < bot
                         and sum(ch.isalnum() for ch in w.text) >= 2), key=lambda w: w.bbox.y1)
        mid = (top + bot) / 2
        lo, hi = top + 0.3 * (bot - top), top + 0.7 * (bot - top)
        # the least-crossed horizontal line near the middle of the row (words of two stacked rows can touch)
        grid_y = np.arange(lo, hi, 2.0)
        if grid_y.size == 0:
            continue
        starts = np.array([w.bbox.y1 - y1 for w in inside])
        ends = np.array([w.bbox.y2 - y1 for w in inside])
        cover = np.array([int(np.count_nonzero((starts < yy) & (ends > yy))) for yy in grid_y])
        cuts = [float(yy) for yy, cv in zip(grid_y, cover) if cv == cover.min()]
        for cut in sorted(cuts, key=lambda c: abs(c - mid))[:1]:
            above, below = set(), set()
            for w in inside:
                cx = (w.bbox.x1 + w.bbox.x2) / 2 - x1
                ci = bisect.bisect_right(col_b, cx) - 1
                ((above if (w.bbox.y1 + w.bbox.y2) / 2 - y1 < cut else below)).add(ci)
            if len(above & below) >= 2:
                out.append(cut)
                break
    return sorted(out)


def _merge_group_header(grid, confs, row_b, col_b, vert, words, x1, y1):
    """Two-tier header: a group label ("Previously shown in MISO") sits over several columns and the sub-headers
    ("Chassis No", "Engine No") on the next row. The vertical rules tell where the group cell spans (the rule is
    absent in the top row). The two rows become one header row, "group / sub-header", so every column has a name."""
    n_rows, n_cols = len(grid), len(col_b) - 1
    if n_rows < 3 or n_cols < 4:
        return grid, confs, row_b
    top, bot = int(row_b[0]), int(row_b[1])
    if bot - top < 8:
        return grid, confs, row_b
    h, w = vert.shape

    def covered(x: float, ya: int, yb: int) -> float:
        xa, xb = max(0, int(x) - 4), min(w, int(x) + 5)
        band = vert[max(0, ya):min(h, yb), xa:xb]
        return float(np.count_nonzero(band.any(axis=1))) / max(1, band.shape[0]) if band.size else 0.0

    spans, start = [], 0
    for k in range(1, n_cols):
        if covered(col_b[k], top + 2, bot - 2) < 0.5:             # no rule in the group row here: one cell continues across
            continue
        spans.append((start, k - 1))
        start = k
    spans.append((start, n_cols - 1))
    multi = [(a, b) for a, b in spans if b > a]
    if not multi or any(b - a + 1 > max(2, n_cols // 2) for a, b in multi):   # a line spanning the table is a title, not a group
        return grid, confs, row_b
    sub = grid[1]
    for a, b in multi:
        under = [sub[c].strip() for c in range(a, b + 1)]
        if sum(1 for u in under if u) < (b - a + 1) or any(len(u) > 28 or sum(ch.isdigit() for ch in u) > 2 for u in under):
            return grid, confs, row_b                                  # not a header's second tier: leave the grid alone
    new0 = list(grid[0])
    for a, b in spans:
        if b > a:
            gw = [(w_.bbox.x1, w_.bbox.y1, w_.bbox.x2, w_.bbox.y2, w_.text) for w_ in words
                  if a <= bisect.bisect_right(col_b, (w_.bbox.x1 + w_.bbox.x2) / 2 - x1) - 1 <= b
                  and top <= (w_.bbox.y1 + w_.bbox.y2) / 2 - y1 < bot]
            label = " ".join(ln.text for ln in words_to_lines(gw)).strip()
            if len(label) > 40:
                return grid, confs, row_b
            for c in range(a, b + 1):
                new0[c] = f"{label} / {grid[1][c].strip()}".strip(" /")
        else:
            c = a
            new0[c] = f"{grid[0][c].strip()} {grid[1][c].strip()}".strip()
    grid = [new0] + grid[2:]
    row_b = [row_b[0]] + row_b[2:]
    new_confs = {}
    for (r, c), v in confs.items():
        if r == 1:
            if (0, c) not in confs:
                new_confs[(0, c)] = v
        elif r >= 2:
            new_confs[(r - 1, c)] = v
        else:
            new_confs[(r, c)] = v
    return grid, new_confs, row_b


_RULE_NOISE = re.compile(r"^[|¦_\[\]{}!]{1,3}$")     # a ruling line the OCR read as a character


def _merge_rowspans(grid, confs, row_b, col_b, horiz, header_rows=1, skip_boundaries=()):
    """A cell that spans several rows (a remark written once beside a block of rows) has no horizontal rule through
    its column while the other columns do have one. Its text, which the words spread over the rows it covers,
    goes back into the first of those rows."""
    n_rows, n_cols = len(grid), len(col_b) - 1
    h, w = horiz.shape
    if n_rows < header_rows + 3 or n_cols < 3:
        return grid, confs

    def rule(c: int, r: int) -> float:
        xa, xb = max(0, int(col_b[c]) + 3), min(w, int(col_b[c + 1]) - 2)
        ya, yb = max(0, int(row_b[r]) - 4), min(h, int(row_b[r]) + 5)
        band = horiz[ya:yb, xa:xb]
        return float(np.count_nonzero(band.any(axis=0))) / max(1, band.shape[1]) if band.size else 0.0

    synthetic = lambda r: any(abs(row_b[r] - sb) < 3 for sb in skip_boundaries)   # noqa: E731  (a cut we made ourselves)
    for c in range(n_cols):
        # boundaries (between row r-1 and r) with no rule in this column although the rest of the table has one
        gap = {}
        for r in range(header_rows + 1, n_rows):
            if synthetic(r):
                gap[r] = True
                continue
            if rule(c, r) < 0.4:
                others = [rule(o, r) for o in range(n_cols) if o != c]
                gap[r] = sum(1 for v in others if v >= 0.5) >= max(2, int(0.5 * len(others)))
            else:
                gap[r] = False
        r = header_rows + 1
        while r < n_rows:
            if not gap[r]:
                r += 1
                continue
            end = r
            while end + 1 < n_rows and gap[end + 1]:
                end += 1
            # a run of two or more unruled boundaries = one cell over three or more rows (a single one is a faint rule)
            if end - r + 1 >= 2 and not all(synthetic(k) for k in range(r, end + 1)):
                top = r - 1
                for k in range(r, end + 1):
                    if (grid[k][c] or "").strip():
                        grid[top][c] = f"{grid[top][c]} {grid[k][c]}".strip()
                        grid[k][c] = ""
            r = end + 1
    return grid, confs


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
    rot, unrot = _straighten(gray, horiz)
    if rot is not None:                                   # read the grid on the straightened crop, words turned the same way
        gray = cv2.warpAffine(gray, rot, (rw, rh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 15, 10)
        horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, rw // 15), 1)))
        vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, rh // 15))))
        slope = 0.0
    ys = _merge_close(_line_positions(horiz, 0, 0.4 * rw), 5)
    xs = _merge_close(_line_positions(vert, 1, 0.4 * rh), 5)

    words = [w for w in page.words()
             if w.bbox and x1 <= (w.bbox.x1 + w.bbox.x2) / 2 <= x2 and y1 <= (w.bbox.y1 + w.bbox.y2) / 2 <= y2]
    if rot is not None:
        from types import SimpleNamespace
        turned = []
        for w in words:
            b = w.bbox
            a1, b1, a2, b2 = _hull(rot, b.x1 - x1, b.y1 - y1, b.x2 - x1, b.y2 - y1)
            turned.append(SimpleNamespace(text=w.text, confidence=getattr(w, "confidence", None),
                                          bbox=SimpleNamespace(x1=a1 + x1, y1=b1 + y1, x2=a2 + x1, y2=b2 + y1)))
        words = turned
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
    cut_rows: list[float] = []
    if not text_rows and len(ys) >= 3:
        before = set(row_b)
        row_b = _split_tall_rows(row_b, col_b, words, x1, y1)           # a faint rule that left two rows in one
        cut_rows = [v for v in row_b if v not in before]
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
        if _RULE_NOISE.match(text.strip()):
            continue
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

    if len(ys) >= 3 and len(xs) >= 3:
        grid, confs, row_b = _merge_group_header(grid, confs, row_b, col_b, vert, words, x1, y1)
        n_rows = len(grid)
        grid, confs = _merge_rowspans(grid, confs, row_b, col_b, horiz, skip_boundaries=cut_rows)
    # a single speck (a stamp edge, a smudge) in the open strip below / above the last rule is not a row
    while len(grid) >= 4 and n_cols >= 4:
        alnum = lambda c: sum(ch.isalnum() for ch in (c or ""))      # noqa: E731
        solid_cells = [c for c in grid[-1] if alnum(c) >= 2]
        if len(solid_cells) <= 1 and sum(alnum(c) for c in grid[-1]) <= 8:
            grid.pop()
            row_b = row_b[:-2] + row_b[-1:]
            n_rows = len(grid)
        else:
            break
    real = lambda t: bool(re.search(r"[A-Za-z0-9]", t or ""))        # noqa: E731  (box borders read as "|" or "_" are not text)
    keep_rows = [r for r in range(n_rows) if any(real(c) for c in grid[r])]
    keep_cols = [c for c in range(n_cols) if any(real(grid[r][c]) for r in range(n_rows))]
    if len(keep_rows) < 2 or len(keep_cols) < 2:
        return None

    cells = []
    for ri, r in enumerate(keep_rows):
        for ci, c in enumerate(keep_cols):
            ax1, ay1, ax2, ay2 = col_b[c], row_b[r], col_b[c + 1], row_b[r + 1]
            if unrot is not None:                         # back onto the page as scanned
                ax1, ay1, ax2, ay2 = _hull(unrot, ax1, ay1, ax2, ay2)
            cells.append({
                "row": ri, "col": ci, "text": grid[r][c],
                "bbox": {"x1": int(x1 + ax1), "y1": int(y1 + ay1), "x2": int(x1 + ax2), "y2": int(y1 + ay2)},
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
    body = [c for c in t.cells if not c.get("is_header")]
    # a column made of ":" separators is "Label : value" laid out in three columns - a form, whatever its labels are
    per_col: dict[int, list[str]] = {}
    for c in body:
        if "col" in c and (c.get("text") or "").strip():
            per_col.setdefault(c["col"], []).append(c["text"].strip())
    if any(len(v) >= 4 and sum(1 for x in v if x in (":", ":-", "::", ";", ".", ":.")) / len(v) >= 0.6 for v in per_col.values()):
        return True
    # one whole column of "Label : value" cells (the value sits in the label's own cell; the next column starts the
    # right-hand half of the form) - data columns do not look like that
    if any(len(v) >= 6 and sum(1 for x in v if re.search(r"[A-Za-z]\s*:\s*\S", x)) / len(v) >= 0.6 for v in per_col.values()):
        return True
    if body and all("col" in c and "row" in c for c in body):
        n_rows = max(1, len({c["row"] for c in body}))
        filled: dict[int, int] = {}
        for c in body:
            if (c.get("text") or "").strip():
                filled[c["col"]] = filled.get(c["col"], 0) + 1
        solid = {ci for ci, n in filled.items() if n >= 0.4 * n_rows}      # specks of noise in extra columns do not count
        cells = [c.get("text") or "" for c in body if c["col"] in solid]
    else:
        solid = set(range(len(t.headers)))
        cells = [c.get("text") or "" for c in body] or [c for r in t.rows for c in r]
    cells = [c for c in cells if c.strip()]
    if len(solid) > 3 or len(cells) < 2:
        return False
    avg_len = sum(len(c) for c in cells) / len(cells)
    label_runs = sum(len(re.findall(r"[A-Za-z][A-Za-z ./&()']{2,30}\s*:\s", c)) for c in cells) / len(cells)
    if avg_len > 90 and label_runs >= 3:
        return True
    # a checklist laid out as 2-3 columns of "Label : value" cells
    colon_share = sum(1 for c in cells if re.search(r"\w\s*:\s*\S", c)) / len(cells)
    if len(cells) >= 4 and colon_share >= 0.6:
        return True
    # a header row names columns, it does not carry values: "Make : Gypsy" up there means the "table" is a boxed form,
    # and the same pattern in a good part of long cells confirms it (OCR specks may spoil the stricter share above)
    # OCR specks in a side column are short; judged on the real (longer) cells, a form of "Label : value" lines stands out
    long_cells = [c for c in cells if len(c.strip()) >= 12]
    if len(long_cells) >= 6 and sum(1 for c in long_cells if re.search(r"\w\s*:\s*\S", c)) / len(long_cells) >= 0.6:
        return True
    heads = [h for h in t.headers if (h or "").strip()]
    head_colons = sum(1 for h in heads if re.search(r"\w\s*:\s*\S", h))
    if len(heads) >= 2 and head_colons >= 2 and head_colons / len(heads) >= 0.5 and colon_share >= 0.4:
        return True
    return len(cells) >= 8 and colon_share >= 0.45 and avg_len >= 28


def find_text_table_regions(page: PageData) -> list[tuple[int, int, int, int]]:
    """Tables with no ruling lines at all (financial statements, price lists): a run of text lines whose words fall into
    the same three or more columns, separated by gutters that stay empty down the whole run. Prose has no such
    gutters, a form's "Label : value" lines are rejected later as form blocks."""
    words = [w for w in page.words() if w.bbox and (w.text or "").strip()]
    if len(words) < 12:
        return []
    lines = words_to_lines([(w.bbox.x1, w.bbox.y1, w.bbox.x2, w.bbox.y2, w.text) for w in words])
    lines = [ln for ln in lines if ln.words]
    heights = [max(1.0, ln.bbox.y2 - ln.bbox.y1) for ln in lines if ln.bbox]
    if not heights:
        return []
    h_med = median(heights)
    gap_min = 1.6 * h_med                                     # a column gutter is clearly wider than a word space

    def cells_of(ln) -> list[tuple[float, float]]:
        ws = sorted(ln.words, key=lambda w: w.bbox.x1)
        cells = [[ws[0].bbox.x1, ws[0].bbox.x2]]
        for w in ws[1:]:
            if w.bbox.x1 - cells[-1][1] >= gap_min:
                cells.append([w.bbox.x1, w.bbox.x2])
            else:
                cells[-1][1] = max(cells[-1][1], w.bbox.x2)
        return [(a, b) for a, b in cells]

    info = []
    for ln in lines:
        cs = cells_of(ln) if ln.bbox else []
        info.append((ln, cs))
    tabular = [len(cs) >= 3 for _, cs in info]

    regions: list[tuple[int, int, int, int]] = []
    i = 0
    while i < len(info):
        if not tabular[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(info) and tabular[j + 1] and \
                info[j + 1][0].bbox.y1 - info[j][0].bbox.y2 <= 2.2 * h_med:
            j += 1
        run = info[i:j + 1]
        i = j + 1
        if len(run) < 4:
            continue
        # gutters that are empty on (nearly) every line of the run
        x_lo = min(a for _, cs in run for a, _ in cs)
        x_hi = max(b for _, cs in run for _, b in cs)
        n_bins = max(20, int((x_hi - x_lo) / 4))
        occ = np.zeros(n_bins, dtype=np.int32)
        for _, cs in run:
            seen = np.zeros(n_bins, dtype=bool)
            for a, b in cs:
                seen[int((a - x_lo) / (x_hi - x_lo + 1e-6) * n_bins):int((b - x_lo) / (x_hi - x_lo + 1e-6) * n_bins) + 1] = True
            occ += seen
        empty = occ <= max(0, int(0.12 * len(run)))
        gutters, k = 0, 0
        width_bins = gap_min / ((x_hi - x_lo + 1e-6) / n_bins)
        while k < n_bins:
            if empty[k]:
                m = k
                while m + 1 < n_bins and empty[m + 1]:
                    m += 1
                if m - k + 1 >= max(2, 0.8 * width_bins) and k > 0 and m < n_bins - 1:
                    gutters += 1
                k = m + 1
            else:
                k += 1
        if gutters < 2:
            continue
        y_lo = min(ln.bbox.y1 for ln, _ in run)
        y_hi = max(ln.bbox.y2 for ln, _ in run)
        pad = int(0.5 * h_med)
        # one header line directly above the run belongs to it (a header with fewer gaps than the rows)
        k0 = max(0, i - len(run) - 1)
        if k0 < len(info) and info[k0][0] is not run[0][0] and info[k0][0].bbox and y_lo - info[k0][0].bbox.y2 <= 2.2 * h_med \
                and len(info[k0][1]) >= 2:
            y_lo = min(y_lo, info[k0][0].bbox.y1)
        regions.append((int(max(0, x_lo - pad)), int(max(0, y_lo - pad)), int(x_hi + pad), int(y_hi + pad)))
    return regions[:_MAX_TEXT_TABLES]


def _numeric_cell(c: str) -> bool:
    """An amount, quantity, date or serial number: after dropping separators, mostly digits ("1,204.50", "12/03/2025");
    names with a number in them ("155 BH") are not."""
    core = re.sub(r"[\s,.\-/%()$₹]", "", c)
    return 0 < len(core) <= 14 and sum(ch.isdigit() for ch in core) / len(core) >= 0.7


def _plausible_text_table(t: TableResult) -> bool:
    """A table found from text alignment alone has no ruling lines to vouch for it, so it must look like a table:
    three or more columns, four or more rows, mostly filled cells and at least one number-like column. Letterhead
    address blocks and distribution lists are sparse and numberless, so they fail."""
    n_cols = len(t.headers)
    if n_cols < TEXT_ROWS_MIN_COLS or len(t.rows) < TEXT_ROWS_MIN_ROWS - 1:
        return False
    grid = [t.headers] + [list(r) for r in t.rows]
    cells = [str(c or "").strip() for row in grid for c in row]
    if not cells or sum(1 for c in cells if c) / len(cells) < 0.7:
        return False
    for ci in range(n_cols):
        col = [str(r[ci] or "").strip() for r in grid[1:] if ci < len(r)]
        col = [c for c in col if c]
        if col and sum(1 for c in col if _numeric_cell(c)) / len(col) >= 0.6:
            return True
    return False


def extract_page_tables(page: PageData, first_index: int = 1) -> list[TableResult]:
    """All tables on one page. Pages without a ruled table cost one line scan."""
    if page.image is None:
        return []
    out: list[TableResult] = []
    regions = find_table_regions(page.image)
    from_text = False
    if not regions and settings.FAST_TEXT_TABLES_ENABLED:
        regions = find_text_table_regions(page)
        from_text = bool(regions)
        if regions:
            logger.info("fast_text_table_regions", page=page.page_number, regions=len(regions))
    for region in regions:
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
        if t is not None and looks_like_noise_table(t):
            logger.info("fast_table_skipped_noise", page=page.page_number, cols=len(t.headers), rows=len(t.rows))
            t = None
        if t is not None and from_text:
            t.extraction_method = "grid_text"                          # found from text alignment, not from ruling lines
        if t is not None and from_text and not _plausible_text_table(t):
            logger.info("fast_text_table_rejected", page=page.page_number, cols=len(t.headers), rows=len(t.rows))
            t = None
        if t is not None:
            out.append(t)
    return out
