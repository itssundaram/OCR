"""
Table understanding on top of the raw grid (tables.py):

  finalize_tables()   header row, title / footer rows, tables continued over
                      several pages, column types, per-cell checks, summary
  validate_cell()     one cell against its column's spec (also used by the
                      background refiner, so a re-read value is judged by the
                      same rules as the first one)

Nothing here reads the page again. Every cell ends up with
  text        the value to show (cleaned only where the fix is certain)
  raw_text    what OCR returned, when the two differ
  state       accepted | flagged | empty
  reasons     [{code, message, level}]   why it is flagged / what was changed
  suggestion  a proposed replacement the person can apply (never auto-applied)
  confidence  OCR confidence combined with how well the value fits its column
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher, get_close_matches

from app.fast.validate import _NUMBER, parse_date
from app.knowledge.kb import normalize_headers, table_records
from app.pipelines.base import TableResult

# ── vocabulary ────────────────────────────────────────────────────────────────
# General column-title words for forms, registers, invoices, stores lists, rosters... No document type owns this list;
# anything not in it is still found by the structural rule in detect_header (text above data).
_HEADER_WORDS = {
    "sno", "slno", "srno", "serial", "sl", "sr", "ser", "id", "no", "number", "name", "gender", "sex",
    "dob", "date", "birth", "age", "rank", "designation", "department", "address", "phone", "mobile", "email",
    "item", "items", "description", "particulars", "nomenclature", "qty", "quantity", "unit", "units", "rate", "price", "amount",
    "total", "remarks", "remark", "status", "code", "category", "type", "grade", "batch", "branch", "location",
    "from", "to", "ref", "reference", "size", "weight", "value", "uom", "au", "part", "model", "make", "balance",
    "debit", "credit", "tax", "discount", "period", "year", "month", "place", "station", "authority",
}
_EDGE = " \t-_~\u2014\u2013=.,:;|'\"`\u00b4\u2018\u2019\u201c\u201d\u00ab\u00bb*!\\^+<>"
_SERIAL_HDR = re.compile(r"^(s\.?\s?no|sl\.?\s?no|sr\.?\s?no|serial|#|sno)\b", re.I)
_DATE_HDR = re.compile(r"\b(dob|d\.o\.b|date|birth)\b", re.I)
_UNITS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}

# what OCR commonly turns a true character into (observed -> possible truths)
_DIGIT_FIX = {"7": "14", "4": "1", "9": "026", "O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5",
              "s": "5", "B": "8", "Z": "2", "z": "2", "G": "6", "g": "9", "q": "9", "/": "1", "6": "0", "8": "03"}
_LETTER_FIX = {"0": "OD", "1": "IL", "5": "S", "8": "B", "2": "Z", "6": "G"}

ACCEPT_AT = 0.6


def strip_edges(s: str) -> str:
    return (s or "").strip(_EDGE)


def _alnum(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (s or "").lower())


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def words_to_int(text: str) -> int | None:
    toks = re.findall(r"[a-z]+", (text or "").lower())
    total, seen = 0, False
    for t in toks:
        if t in _UNITS:
            total += _UNITS.index(t)
            seen = True
        elif t in _TENS:
            total += _TENS[t]
            seen = True
        elif t == "hundred" and seen:
            total = max(total, 1) * 100
    return total if seen else None


# ── column spec ───────────────────────────────────────────────────────────────
@dataclass
class Verdict:
    text: str
    state: str                                   # accepted | flagged | empty
    reasons: list[dict] = field(default_factory=list)
    suggestion: str | None = None
    confidence: float = 0.0
    fixed: bool = False


def _shape(v: str) -> str:
    return "".join("9" if c.isdigit() else ("A" if c.isupper() else ("a" if c.islower() else c)) for c in v)


def _shape_key(v: str) -> str:
    return _shape(v.upper())


def _reason(code: str, message: str, level: str = "bad") -> dict:
    return {"code": code, "message": message, "level": level}


def _header_hits(cells: list[str], extra: set[str]) -> int:
    hits = 0
    vocab = _HEADER_WORDS | extra
    for c in cells:
        key = _alnum(c)
        if not key:
            continue
        toks = [_alnum(t) for t in re.findall(r"[A-Za-z]+", c)]
        # whole words always; a vocabulary word inside a longer glued word only when it is long enough to mean it
        # ("date" must not fire on "update", "age" not on "message")
        if any(t in vocab for t in toks) or any(len(v) >= 6 and v in key for v in vocab):
            hits += 1
    return hits


def _is_alpha_cell(c: str) -> bool:
    letters = sum(ch.isalpha() for ch in c)
    alnum = sum(ch.isalnum() for ch in c)
    return alnum > 0 and letters / alnum >= 0.7 and len(c.split()) <= 6


def _has_digit(c: str) -> bool:
    return any(ch.isdigit() for ch in c)


def _text_over_data(rows: list[list[str]], i: int, n_cols: int) -> tuple[float, float]:
    """(share of filled cells in row i that are short text, share of columns where text sits above digits).
    A header is words over data; a first data row of names has no digits below it."""
    row = rows[i]
    filled = [(j, c.strip()) for j, c in enumerate(row) if c.strip()]
    if not filled:
        return 0.0, 0.0
    alpha = sum(1 for _, c in filled if _is_alpha_cell(c)) / len(filled)
    body = rows[i + 1:i + 7]
    contrast = 0
    for j, c in filled:
        if not _is_alpha_cell(c) or _has_digit(c):
            continue
        below = [r[j].strip() for r in body if j < len(r) and r[j].strip()]
        if below and sum(_has_digit(b) for b in below) / len(below) >= 0.5:
            contrast += 1
    return alpha, contrast / max(1, n_cols)


def detect_header(rows: list[list[str]], extra_names: set[str]) -> int | None:
    """Index of the header row among the first rows, or None.

    Two independent signals, so no document type has to be known in advance:
      * vocabulary - the row uses ordinary column-title words (or the template's own field names)
      * structure  - the row is short text sitting above columns that hold digits
    """
    best, best_i = 0.0, None
    n_cols = max((len(r) for r in rows), default=0)
    for i, r in enumerate(rows[:8]):
        filled = sum(1 for c in r if c.strip())
        if filled < max(2, n_cols // 2):
            continue
        vocab = _header_hits(r, extra_names) / max(1, n_cols)
        alpha, contrast = _text_over_data(rows, i, n_cols)
        score = 0.0
        if vocab >= 0.3:
            score = vocab + 0.5 * contrast
        elif alpha >= 0.8 and contrast >= 0.34:
            score = 0.3 + 0.5 * contrast               # structural header without known words
        if score > best:
            best, best_i = score, i
    return best_i if best >= 0.3 else None


# ── column inference ──────────────────────────────────────────────────────────
def _canon_key(s: str) -> str:
    return _alnum(_clean_tail(s))


def _clean_tail(s: str) -> str:
    """Remove edge junk and trailing 1-2 character tokens that carry digits/punctuation."""
    s = _norm_ws(strip_edges(s))
    toks = s.split(" ")
    while len(toks) > 1 and len(toks[-1]) <= 2 and not toks[-1].isalpha():
        toks.pop()
    return " ".join(toks)


def infer_column(index: int, name: str, values: list[str], hint_kind: str = "") -> dict:
    vals = [v for v in (strip_edges(v) for v in values) if v]
    spec: dict = {"index": index, "name": name, "kind": "text", "enum": [], "shape": None, "year_median": None}
    if not vals:
        spec["kind"] = "empty"
        return spec
    n = len(vals)
    ints = sum(1 for v in vals if re.fullmatch(r"\d{1,4}", v))
    digit_ratio = sum(1 for v in vals if re.search(r"\d", v)) / n
    dates = [parse_date(v) for v in vals]
    date_ok = sum(1 for d in dates if d)
    hdr = name or ""
    if _SERIAL_HDR.match(hdr.strip()) or (ints / n >= 0.6 and index == 0):
        spec["kind"] = "serial"
        return spec
    if _DATE_HDR.search(hdr) or hint_kind == "date" or date_ok / n >= 0.5:
        spec["kind"] = "date"
        years = sorted(d.year for d in dates if d)
        if years:
            spec["year_median"] = years[len(years) // 2]
        return spec
    # id-like: one dominant shape that mixes characters or is a fixed-width number
    shapes = Counter(_shape_key(strip_edges(v)) for v in vals if len(strip_edges(v)) >= 3)
    if shapes:
        top, cnt = shapes.most_common(1)[0]
        if cnt / n >= 0.5 and cnt >= 3 and re.search(r"9", top) and len(top) >= 3:
            spec["kind"] = "id"
            spec["shape"] = top
            return spec
    # enum: few distinct normalised values that repeat
    keys: dict[str, Counter] = {}
    for v in vals:
        keys.setdefault(_canon_key(v), Counter())[_norm_ws(strip_edges(_clean_tail(v)))] += 1
    freq = Counter({k: sum(c.values()) for k, c in keys.items() if k})
    repeated = [k for k, c in freq.items() if c >= 2]
    if n >= 6 and repeated and len(repeated) <= 12 and sum(freq[k] for k in repeated) / n >= 0.8 and digit_ratio < 0.2:
        spec["kind"] = "enum"
        spec["enum"] = [keys[k].most_common(1)[0][0] for k in repeated]
        return spec
    numeric = sum(1 for v in vals if _NUMBER.match(v))
    if numeric / n >= 0.7:
        spec["kind"] = "number"
        return spec
    alpha = [sum(c.isalpha() or c in " .'-" for c in v) / max(1, len(v)) for v in vals]
    if sum(a >= 0.9 for a in alpha) / n >= 0.7 and digit_ratio < 0.15:
        spec["kind"] = "name"
        return spec
    return spec


# ── per-cell checks ───────────────────────────────────────────────────────────
def _date_candidates(text: str, year_median: int | None) -> list[str]:
    """Dates reachable by changing ONE character the way OCR usually errs."""
    m = re.fullmatch(r"(\d{1,2})[-./ ]+(\d{1,2})[-./ ]+(\d{2,4})", text)
    if not m:
        return []
    digits = list(text)
    out = []
    for i, ch in enumerate(digits):
        for true in _DIGIT_FIX.get(ch, ""):
            trial = digits[:]
            trial[i] = true
            cand = "".join(trial)
            d = parse_date(cand)
            if d and (year_median is None or abs(d.year - year_median) <= 15):
                out.append(cand)
    return sorted(set(out))


def _fmt_date(text: str) -> str:
    m = re.fullmatch(r"(\d{1,2})[-./ ]+(\d{1,2})[-./ ]+(\d{2,4})", text)
    return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}-{m.group(3)}" if m else text


def _shape_candidates(text: str, shape: str) -> list[str]:
    """Strings of the dominant shape reachable by dropping one stray character
    or changing one the way OCR usually errs."""
    out: set[str] = set()
    up = text.upper()
    if len(up) == len(shape) + 1:
        for i in range(len(up)):
            cand = up[:i] + up[i + 1:]
            if _shape_key(cand) == shape:
                out.add(cand)
    if len(up) == len(shape):
        for i, ch in enumerate(up):
            table = _DIGIT_FIX if shape[i] == "9" else _LETTER_FIX if shape[i] == "A" else {}
            for true in table.get(ch, ""):
                cand = up[:i] + true + up[i + 1:]
                if _shape_key(cand) == shape:
                    out.add(cand)
    return sorted(out)


def _combine(ocr: float | None, evidence: float) -> float:
    o = 0.5 if ocr is None else max(0.0, min(1.0, ocr))
    base = 0.45 * o + 0.55 * evidence
    if evidence >= 0.85:                       # fits the sequence / the column's closed list exactly
        base = max(base, 0.75)
    return round(max(0.0, min(1.0, base)), 3)


def validate_cell(spec: dict, raw: str, ocr: float | None, ctx: dict | None = None) -> Verdict:
    """Judge one value against its column. `ctx` carries the sequence state for
    serial columns: {"expected": int|None, "next": int|None}."""
    ctx = ctx or {}
    raw = raw or ""
    kind = spec.get("kind", "text")
    text = _norm_ws(raw)
    if not strip_edges(text) and not re.search(r"[0-9A-Za-z]", text):
        return Verdict("", "empty", [], None, 0.0)
    reasons: list[dict] = []
    suggestion: str | None = None
    fixed = False
    evidence = 0.5
    bad = False

    if kind == "serial":
        digits = "".join(re.findall(r"\d+", text))
        exp = ctx.get("expected")
        nxt = ctx.get("next")
        n = int(digits) if digits and len(digits) <= 4 else None
        if exp is None:
            if n is not None and re.fullmatch(r"\d{1,4}", strip_edges(text)):
                text, evidence = str(n), 0.8
            elif n is not None:
                text, evidence, fixed = str(n), 0.55, True
                reasons.append(_reason("cleaned", f"Read as '{raw.strip()}'", "info"))
            else:
                bad, evidence = True, 0.0
                reasons.append(_reason("not_serial", "Serial number is not readable"))
        elif n == exp:
            text, evidence = str(exp), 0.92
            if not re.fullmatch(r"\d{1,4}", strip_edges(raw)):
                fixed = True
                reasons.append(_reason("cleaned", f"Read as '{raw.strip()}'; fits the sequence", "info"))
        else:
            near = bool(digits) and (digits.startswith(str(exp)) or digits.endswith(str(exp)) or
                                     any(digits[:i] + digits[i + 1:] == str(exp) for i in range(len(digits))))
            subst = bool(digits) and len(digits) == len(str(exp)) and sum(a != b for a, b in zip(digits, str(exp))) == 1 and \
                all(b in _DIGIT_FIX.get(a, "") for a, b in zip(digits, str(exp)) if a != b)
            ahead = nxt is not None and nxt == exp + 1
            if near or subst or ahead:
                text, evidence, fixed = str(exp), 0.85, True
                reasons.append(_reason("sequence_fix", f"Read as '{raw.strip()}'; sequence says {exp}", "info"))
            elif n is not None and nxt is not None and nxt == n + 1 and exp < n <= exp + 5:
                text, evidence = str(n), 0.7
                reasons.append(_reason("sequence_gap", f"Serial jumps from {exp - 1} to {n}", "info"))
            else:
                bad, evidence = True, 0.1
                suggestion = str(exp)
                reasons.append(_reason("sequence_break", f"Expected {exp} here, read '{raw.strip()}'"))
    elif kind == "date":
        cleaned = strip_edges(text)
        cleaned = re.sub(r"\s*([-./])\s*", r"\1", cleaned)
        d = parse_date(cleaned)
        ym = spec.get("year_median")
        if d:
            text = _fmt_date(cleaned)
            evidence = 0.7
            if ym and abs(d.year - ym) > 15:
                bad, evidence = True, 0.2
                reasons.append(_reason("date_outlier", f"Year {d.year} is far from the other dates (around {ym})"))
        else:
            bad, evidence = True, 0.0
            cands = _date_candidates(cleaned, ym)
            m = re.fullmatch(r"(\d{1,2})[-./ ]+(\d{1,2})[-./ ]+(\d{2,4})", cleaned)
            if m:
                dd, mm = int(m.group(1)), int(m.group(2))
                what = f"day {dd}" if not 1 <= dd <= 31 else f"month {mm}"
                reasons.append(_reason("date_invalid", f"Not a real date ({what} is impossible)"))
            else:
                reasons.append(_reason("date_unreadable", "Cannot be read as a date"))
            if len(cands) == 1:
                suggestion = _fmt_date(cands[0])
                reasons[-1]["message"] += f"; likely {suggestion}"
    elif kind == "id":
        shape = spec.get("shape") or ""
        cleaned = _norm_ws(strip_edges(text)).replace(" ", "")
        if _shape_key(cleaned) == shape:
            text = cleaned.upper() if shape.upper() == shape else cleaned
            evidence = 0.8
            if text != raw.strip():
                fixed = True
                reasons.append(_reason("cleaned", f"Read as '{raw.strip()}'", "info"))
        else:
            bad, evidence = True, 0.1
            cands = _shape_candidates(cleaned, shape)
            reasons.append(_reason("pattern_mismatch", f"Does not match the usual format {shape.replace('9', '#')}"))
            if len(cands) == 1:
                suggestion = cands[0]
                reasons[-1]["message"] += f"; likely {suggestion}"
            text = cleaned
    elif kind == "enum":
        allowed = spec.get("enum") or []
        key = _canon_key(text)
        by_key = {_canon_key(a): a for a in allowed}
        if key in by_key:
            text, evidence = by_key[key], 0.92
            if text != text.strip() or _norm_ws(strip_edges(raw)) != text:
                fixed = True
                reasons.append(_reason("cleaned", f"Read as '{raw.strip()}'", "info"))
        else:
            bad, evidence = True, 0.1
            close = get_close_matches(key, list(by_key), n=1, cutoff=0.7)
            if close:
                suggestion = by_key[close[0]]
                reasons.append(_reason("not_in_list", f"Not one of the usual values; closest is {suggestion}"))
            else:
                reasons.append(_reason("not_in_list", "Not one of: " + ", ".join(allowed[:6])))
            text = _norm_ws(strip_edges(text))
    elif kind == "number":
        cleaned = strip_edges(text)
        if _NUMBER.match(cleaned):
            text, evidence = cleaned, 0.7
        else:
            bad, evidence = True, 0.1
            reasons.append(_reason("not_number", "Not a number"))
    elif kind == "name":
        cleaned = _norm_ws(strip_edges(text))
        letters = sum(c.isalpha() for c in cleaned)
        evidence = 0.7
        if cleaned != text:
            fixed = True
            reasons.append(_reason("cleaned", f"Read as '{raw.strip()}'", "info"))
        if re.search(r"\d", cleaned) or letters / max(1, len(cleaned)) < 0.7:
            bad, evidence = True, 0.15
            reasons.append(_reason("not_a_name", "Contains digits or symbols"))
        elif any(len(tk) == 1 and tk.islower() for tk in cleaned.split()):
            bad, evidence = True, 0.2
            reasons.append(_reason("stray_char", "A stray character is mixed into the name"))
        elif " " not in cleaned and len(cleaned) > 14:
            bad, evidence = True, 0.3
            reasons.append(_reason("joined_words", "Words may be run together"))
        text = cleaned
    elif kind == "nonlatin":
        text = _norm_ws(text.strip(" \t_~|`*!\\^"))
        bad, evidence = True, 0.2
        reasons.append(_reason("non_latin", "Probably Hindi or handwriting; the printed-text reader cannot read it", "warn"))
    else:                                        # free text
        light = _norm_ws(text.strip(" \t_~\u2014\u2013=|`\u00ab\u00bb*!\\^"))
        if light != text:
            fixed = True
            reasons.append(_reason("cleaned", f"Read as '{raw.strip()}'", "info"))
        text = light
        chars = re.sub(r"\s", "", text)
        evidence = 0.6
        core = re.sub(r"[()&.,/\-]", "", chars)
        if core and sum(c.isalnum() for c in core) / len(core) < 0.6:
            bad, evidence = True, 0.1
            reasons.append(_reason("garbled", "Mostly symbols; probably misread"))

    conf = _combine(ocr, evidence)
    if bad:
        state = "flagged"
        conf = min(conf, 0.45)
    elif conf < ACCEPT_AT:
        state = "flagged"
        reasons.append(_reason("weak_read", f"Low-confidence read ({int((ocr or 0) * 100)}%)", "warn"))
    else:
        state = "accepted"
    return Verdict(text, state, reasons, suggestion, conf, fixed)


# ── table assembly ────────────────────────────────────────────────────────────
def _aligned(a: TableResult, b: TableResult, widths: dict[int, int]) -> bool:
    ea, eb = a.meta.get("col_edges") or [], b.meta.get("col_edges") or []
    if len(ea) != len(eb) or len(ea) < 3:
        return False
    wa, wb = widths.get(a.page_number, 1), widths.get(b.page_number, 1)
    return all(abs(x / wa - y / wb) <= 0.03 for x, y in zip(ea, eb))


def _grid_cells(t: TableResult) -> list[list[dict]]:
    n_rows = len(t.rows) + 1
    n_cols = len(t.headers)
    grid: list[list[dict | None]] = [[None] * n_cols for _ in range(n_rows)]
    for c in t.cells:
        if c["row"] < n_rows and c["col"] < n_cols:
            grid[c["row"]][c["col"]] = c
    return grid  # type: ignore[return-value]


def _first_text(cells: list[dict]) -> str:
    return " ".join(c["text"] for c in cells if c and c["text"].strip())


def _body_row_is_junk(row: list[dict]) -> bool:
    filled = [c for c in row if c and re.search(r"[0-9A-Za-z]", c["text"])]
    return len(filled) == 1 and len(filled[0]["text"].strip()) <= 3 and (filled[0].get("confidence") or 0) < 0.5


def finalize_tables(tables: list[TableResult], pages: dict, template_fields: list[dict] | None = None) -> list[TableResult]:
    extra = set()
    for f in template_fields or []:
        for k in ("name", "label", "display_name"):
            v = _alnum(str(f.get(k) or ""))
            if len(v) >= 3:
                extra.add(v)
    widths = {n: p.width for n, p in pages.items()}

    # 1. strip title rows above the header and footer rows below the body, per raw table
    parts: list[dict] = []
    for t in sorted(tables, key=lambda x: (x.page_number, x.table_index)):
        grid = _grid_cells(t)
        text_rows = [[(c["text"] if c else "") for c in r] for r in grid]
        h = detect_header(text_rows, extra)
        title_rows = grid[:h] if h else []
        header_row = grid[h] if h is not None else None
        body = grid[(h + 1) if h is not None else 0:]
        if h is None and grid:
            # no header recognised: keep row 0 as header only if it holds no data-like cells
            first = [c["text"] for c in grid[0] if c]
            if first and not any(re.fullmatch(r"\W*\d[\d\W]*", x.strip()) for x in first if x.strip()):
                header_row, body = grid[0], grid[1:]
        footer: list[str] = []
        while body and re.match(r"^\W*total\b", _first_text(body[-1]), re.I):
            footer.insert(0, _first_text(body.pop()))
        body = [r for r in body if any(c and c["text"].strip() for c in r) and not _body_row_is_junk(r)]
        parts.append({"t": t, "header": header_row, "body": body, "title": [_first_text(r) for r in title_rows if _first_text(r)],
                      "footer": footer})

    # 2. join tables continued over consecutive pages
    merged: list[dict] = []
    for p in parts:
        prev = merged[-1] if merged else None
        if prev and p["t"].page_number == prev["pages"][-1] + 1 and _aligned(prev["t"], p["t"], widths) and \
                len(prev["t"].headers) == len(p["t"].headers):
            prev["body"] += p["body"]
            prev["pages"].append(p["t"].page_number)
            prev["footer"] += p["footer"]
            prev["headers_alt"].append(p["header"])
            continue
        merged.append({**p, "pages": [p["t"].page_number], "headers_alt": [p["header"]]})

    # 3. analyse each table
    out: list[TableResult] = []
    for i, m in enumerate(merged, start=1):
        out.append(_analyse(m, i, pages))
    return out


def _header_cleanliness(cell: dict | None) -> float:
    if not cell:
        return 0.0
    txt = cell["text"]
    letters = sum(c.isalpha() for c in txt)
    return (cell.get("confidence") or 0.0) * 0.5 + (letters / max(1, len(txt))) * 0.5 + (0.3 if _alnum(txt) and _header_hits([txt], set()) else 0)


def _header_is_clear(text: str, confidence: float | None) -> bool:
    """A header cell is trusted when it reads as words: a known column word, or plain letters the OCR was
    reasonably sure of. (Not only the vocabulary: a stores list or a roster can title its columns anything.)"""
    if _header_hits([text], set()) > 0:
        return True
    letters = sum(ch.isalpha() for ch in text)
    return len(text.strip()) >= 3 and letters / max(1, len(text.replace(" ", ""))) >= 0.8 and (confidence is None or confidence >= 0.6)


def _analyse(m: dict, index: int, pages: dict) -> TableResult:
    t: TableResult = m["t"]
    n_cols = len(t.headers)
    # header text: the cleanest of the repeated headers
    headers: list[str] = []
    header_cells: list[dict | None] = []
    for c in range(n_cols):
        cands = [h[c] for h in m["headers_alt"] if h and c < len(h) and h[c]]
        best = max(cands, key=_header_cleanliness) if cands else None
        header_cells.append(best)
        txt = _norm_ws(strip_edges(best["text"])) if best else ""
        headers.append(txt or f"Column {c + 1}")

    try:                                           # TMS / MMS / PSG header vocabulary (app/knowledge): canonical ids + fused-header repair
        known = normalize_headers(headers)
    except Exception:
        known = [{"text": h, "id": "", "kind": "", "raw": h} for h in headers]
    headers = [k["text"] for k in known]

    body = m["body"]
    specs = [infer_column(c, headers[c], [(r[c]["text"] if r[c] else "") for r in body], known[c]["kind"]) for c in range(n_cols)]
    for sp, k in zip(specs, known):
        if k["id"]:
            sp["canonical"] = k["id"]
            if k["text"] != k["raw"]:
                sp["printed"] = k["raw"]

    for sp in specs:                              # a column the printed-text reader cannot read at all
        confs = sorted((r[sp["index"]].get("confidence") or 0.0) for r in body if r[sp["index"]] and r[sp["index"]]["text"].strip())
        if sp["kind"] in ("name", "text") and len(confs) >= 8 and confs[len(confs) // 2] < 0.4:
            sp["kind"] = "nonlatin"

    cells_out: list[dict] = []
    for c in range(n_cols):
        hc = header_cells[c]
        clear = bool(hc) and _header_is_clear(headers[c], (hc or {}).get("confidence"))
        cells_out.append({
            "row": 0, "col": c, "text": headers[c], "bbox": (hc or {}).get("bbox"), "page": (hc or {}).get("page", t.page_number),
            "is_header": True, "confidence": (hc or {}).get("confidence"), "state": "accepted" if clear else "flagged",
            "reasons": [] if clear else [_reason("header_unclear", "Header text is not clearly readable", "warn")], "rev": 0,
        })

    rows_text: list[list[str]] = []
    counts = [{"accepted": 0, "flagged": 0, "empty": 0} for _ in range(n_cols)]
    serial_cols = [s["index"] for s in specs if s["kind"] == "serial"]
    serial_vals: list[int | None] = []
    expected: dict[int, int | None] = {c: None for c in serial_cols}

    def _next_int(ri: int, col: int) -> int | None:
        for r2 in body[ri + 1: ri + 3]:
            cell = r2[col]
            d = "".join(re.findall(r"\d+", cell["text"])) if cell else ""
            if d and len(d) <= 4:
                return int(d)
        return None

    for ri, row in enumerate(body, start=1):
        line: list[str] = []
        for c in range(n_cols):
            cell = row[c]
            raw = cell["text"] if cell else ""
            ctx = None
            if specs[c]["kind"] == "serial":
                ctx = {"expected": expected[c], "next": _next_int(ri - 1, c)}
            v = validate_cell(specs[c], raw, (cell or {}).get("confidence"), ctx)
            if specs[c]["kind"] == "serial" and v.state != "empty":
                d = re.sub(r"\D", "", v.text)
                broke = any(r["code"] in ("sequence_break", "not_serial") for r in v.reasons)
                if d and not broke and len(d) <= 4:
                    expected[c] = int(d) + 1
                elif expected[c] is not None:
                    expected[c] += 1
            d = {
                "row": ri, "col": c, "text": v.text, "bbox": (cell or {}).get("bbox"), "page": (cell or {}).get("page", t.page_number),
                "is_header": False, "confidence": v.confidence, "ocr_confidence": (cell or {}).get("confidence"),
                "state": v.state, "reasons": v.reasons, "rev": 0,
            }
            if v.suggestion:
                d["suggestion"] = v.suggestion
            if v.text != raw.strip():
                d["raw_text"] = raw
            cells_out.append(d)
            counts[c][v.state] += 1
            line.append(v.text)
        rows_text.append(line)
        if serial_cols:
            sc = next(x for x in cells_out[-n_cols:] if x["col"] == serial_cols[0])
            broke = any(r["code"] in ("sequence_break", "not_serial") for r in sc["reasons"])
            sv = re.findall(r"\d+", sc["text"])
            serial_vals.append(int(sv[0]) if sv and not broke and len(sv[0]) <= 4 else None)

    quality = _quality(m, specs, counts, rows_text, serial_vals, headers, pages)
    try:
        records = table_records(known, rows_text)
    except Exception:
        records = []
    meta = {
        "pages": m["pages"], "title_text": " ".join(x for x in m["title"] if sum(ch.isalpha() for ch in x) / max(1, len(x)) >= 0.6 and len(x) >= 8)[:300], "footer_text": " ".join(m["footer"])[:300],
        "quality": quality, "columns": specs, "col_edges": t.meta.get("col_edges"), **({"records": records} if records else {}),
        "header_confidence": round(sum((c or {}).get("confidence") or 0 for c in header_cells) / max(1, n_cols), 3),
    }
    flagged = sum(c["flagged"] for c in counts)
    total = max(1, sum(c["accepted"] + c["flagged"] for c in counts))
    return TableResult(
        table_index=index, page_number=m["pages"][0], headers=headers, rows=rows_text,
        confidence=round(1 - flagged / total, 3), bbox=t.bbox, extraction_method=t.extraction_method,
        cells=cells_out, markdown=_md_simple(headers, rows_text), meta=meta,
    )


def _md_simple(headers: list[str], rows: list[list[str]]) -> str:
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    md = f"| {' | '.join(esc(h) for h in headers)} |\n| {' | '.join(['---'] * len(headers))} |\n"
    return md + "".join(f"| {' | '.join(esc(c) for c in r)} |\n" for r in rows)


def _quality(m: dict, specs: list[dict], counts: list[dict], rows: list[list[str]], serial_vals: list[int | None],
             headers: list[str], pages: dict) -> dict:
    accepted = sum(c["accepted"] for c in counts)
    flagged = sum(c["flagged"] for c in counts)
    empty = sum(c["empty"] for c in counts)
    q: dict = {
        "rows": len(rows), "cols": len(headers), "accepted": accepted, "flagged": flagged, "empty": empty,
        "columns": [{"index": s["index"], "name": headers[s["index"]], "kind": s["kind"],
                     "accepted": counts[s["index"]]["accepted"], "flagged": counts[s["index"]]["flagged"]} for s in specs],
        "notes": [],
    }
    vals = [v for v in serial_vals if v is not None]
    if vals:
        gaps = [(a, b) for a, b in zip(vals, vals[1:]) if b != a + 1]
        q["serial"] = {"first": vals[0], "last": vals[-1], "gaps": [{"after": a, "next": b} for a, b in gaps][:10]}
        if gaps:
            q["notes"].append(f"Serial numbers skip after {gaps[0][0]}" + (f" (+{len(gaps) - 1} more)" if len(gaps) > 1 else ""))
    # expected count from a TOTAL line
    text = " ".join(m["footer"])
    expected = None
    mm = re.search(r"\(\s*(\d{1,5})\s*\)", text) or re.search(r"\b(\d{1,5})\b", text)
    if mm:
        expected = int(mm.group(1))
    elif text:
        expected = words_to_int(text.replace("TOTAL", ""))
    if expected is None:                          # a total printed outside the grid
        for n in m["pages"][-1:]:
            pg = pages.get(n)
            hit = re.search(r"total[^()\n]{0,40}\(\s*(\d{1,5})\s*\)", pg.text if pg else "", re.I)
            if hit:
                expected, text = int(hit.group(1)), hit.group(0)
    if expected is not None:
        q["count_check"] = {"expected": expected, "found": len(rows), "ok": expected == len(rows), "source": text.strip()[:80]}
        if expected != len(rows):
            q["notes"].append(f"Document says {expected} rows, {len(rows)} were read")
    return q


def refresh_records(table: dict) -> dict:
    """Rebuild meta.records (the per-row records keyed by canonical column ids) from the table's CURRENT headers and
    rows, so a corrected cell shows up in them. Removes the key when the columns are not recognised."""
    meta = table.get("meta")
    if not isinstance(meta, dict):
        return table
    try:
        records = table_records(normalize_headers([str(h or "") for h in table.get("headers") or []]), table.get("rows") or [])
    except Exception:
        records = []
    if records:
        meta["records"] = records
    else:
        meta.pop("records", None)
    return table


def recount_quality(table: dict) -> dict:
    """Refresh the per-table summary from the stored cells (after an edit or a
    background re-read). Pure; works on the dict that is saved in the result."""
    refresh_records(table)
    meta = table.get("meta") or {}
    q = meta.get("quality")
    if not q:
        return table
    cols = {c["index"]: c for c in q.get("columns", [])}
    for c in cols.values():
        c["accepted"] = c["flagged"] = 0
    acc = flg = emp = 0
    serial_col = next((c["index"] for c in q.get("columns", []) if c.get("kind") == "serial"), None)
    serial_vals: dict[int, int] = {}
    for cell in table.get("cells") or []:
        if cell.get("is_header"):
            continue
        st = cell.get("state") or ("accepted" if (cell.get("confidence") or 0) >= ACCEPT_AT else "flagged")
        if cell.get("edited"):
            st = "accepted"
        if not str(cell.get("text") or "").strip():
            st = "empty"
        if st == "accepted":
            acc += 1
        elif st == "flagged":
            flg += 1
        else:
            emp += 1
        if cell["col"] in cols and st in ("accepted", "flagged"):
            cols[cell["col"]]["accepted" if st == "accepted" else "flagged"] += 1
        if serial_col is not None and cell["col"] == serial_col:
            d = re.sub(r"\D", "", str(cell.get("text") or ""))
            if d and len(d) <= 4 and st != "empty" and not any(r["code"] == "sequence_break" for r in cell.get("reasons") or []):
                serial_vals[cell["row"]] = int(d)
    q.update({"accepted": acc, "flagged": flg, "empty": emp, "rows": len(table.get("rows") or [])})
    if serial_col is not None and serial_vals:
        ordered = [serial_vals[r] for r in sorted(serial_vals)]
        gaps = [(a, b) for a, b in zip(ordered, ordered[1:]) if b != a + 1]
        q["serial"] = {"first": ordered[0], "last": ordered[-1], "gaps": [{"after": a, "next": b} for a, b in gaps][:10]}
    cc = q.get("count_check")
    if cc:
        cc["found"] = q["rows"]
        cc["ok"] = cc["expected"] == q["rows"]
    # the header may have been re-read
    for c in table.get("cells") or []:
        if c.get("is_header") and c["col"] in cols:
            cols[c["col"]]["name"] = c.get("text", cols[c["col"]]["name"])
    return table
