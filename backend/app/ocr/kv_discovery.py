"""
DOCINT — Key/Value pair discovery
=================================
Scans OCR output (line + word geometry from the Tesseract engine) for
"Label: value" pairs anywhere on a document — "Tele: 2309 4765",
"Ref No: 00961/DGDS-1", "Date: 16 Mar 2020" — and returns them as flat
key : value records with the value's page coordinates.

Pure functions only: no DB, no models, no I/O. Takes already-computed
OCRLine objects, so it can be unit-tested with hand-built geometry.

How it works
  1. Every OCR line is split into *segments* wherever the horizontal gap
     between two words is much wider than normal word spacing. Tesseract
     (psm 6) happily joins a left-column fragment and a right-column
     fragment into one "line"; segments keep them apart.
  2. Inside a segment a *label* is a short run of words (<= 3, or <= 5 when
     it starts the segment) ending in a colon. Everything after the colon,
     up to the next label in the same segment, is that label's value.
  3. A label with nothing after the colon takes its value from the segment
     to its right on the same row, or the segment directly beneath it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median

from app.ocr.base import OCRLine

_COLONS = (":", "：")
# A lone token that stands in for the colon: Tesseract often reads a typewriter
# colon as ">" or ";" (and "=" is how some forms print it).
_LONE_COLONS = (":", "：", ":-", ":—", ">", ";", "=")
_URL_SCHEMES = {"http", "https", "ftp", "mailto", "file", "tel", "www"}
_CONNECTORS = {"of", "and", "to", "for", "no", "the", "in", "on", "by", "&", "/"}
_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.'&/()\-]*$")
_INLINE_RE = re.compile(r"^([A-Za-z][A-Za-z.\-/]*)[:：](\S.*)$")
_EDGE_JUNK = " \t_~|-–—.,;*·•"


@dataclass
class DiscoveredPair:
    label: str
    value: str
    confidence: float
    page_number: int
    bbox: dict | None = None        # union box of the value words (page pixels)
    label_bbox: dict | None = None  # union box of the label words
    kind: str = "colon"             # "colon" = printed "Label: value"; "bare" = label and value with no colon between them


@dataclass
class _Tok:
    text: str
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float


@dataclass
class _Segment:
    toks: list[_Tok]
    height: float
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    pairs: list["_RawPair"] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.x1 = min(t.x1 for t in self.toks)
        self.y1 = min(t.y1 for t in self.toks)
        self.x2 = max(t.x2 for t in self.toks)
        self.y2 = max(t.y2 for t in self.toks)

    @property
    def text(self) -> str:
        return " ".join(t.text for t in self.toks)

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0


@dataclass
class _RawPair:
    label_toks: list[_Tok]
    label_text: str
    value_toks: list[_Tok]
    inline_prefix: str = ""
    inline_conf: float | None = None


def _union(toks: list[_Tok]) -> dict | None:
    if not toks:
        return None
    return {
        "x1": round(min(t.x1 for t in toks)),
        "y1": round(min(t.y1 for t in toks)),
        "x2": round(max(t.x2 for t in toks)),
        "y2": round(max(t.y2 for t in toks)),
    }


def _clean(text: str) -> str:
    return text.strip(_EDGE_JUNK).strip()


def _segments(line: OCRLine) -> list[_Segment]:
    toks = [
        _Tok(w.text.strip(), w.bbox.x1, w.bbox.y1, w.bbox.x2, w.bbox.y2,
             float(w.confidence if w.confidence is not None else 0.0))
        for w in (line.words or [])
        if w.text and w.text.strip() and w.bbox is not None
    ]
    if not toks:
        return []
    toks.sort(key=lambda t: t.x1)
    h = median(max(1.0, t.y2 - t.y1) for t in toks)
    gap_limit = max(2.2 * h, 30.0)

    groups: list[list[_Tok]] = [[toks[0]]]
    for prev, cur in zip(toks, toks[1:]):
        # typed forms often leave a wide gap between the label and its colon
        # ("OBD Number          : 0082830253"); a colon always stays with its label
        if cur.x1 - prev.x2 > gap_limit and not (cur.text.startswith(_COLONS) or cur.text in _LONE_COLONS):
            groups.append([cur])
        else:
            groups[-1].append(cur)
    return [_Segment(g, h) for g in groups]


def _is_label_word(text: str) -> bool:
    if not _WORD_RE.match(text):
        return False
    if text[-1] in ",;":
        return False
    if text.lower() in _URL_SCHEMES:
        return False
    # lowercase words are only acceptable as connectors ("Date of Birth")
    if text[0].islower() and text.lower() not in _CONNECTORS:
        return False
    return True


def _find_colons(toks: list[_Tok]) -> list[tuple[int, str, str, float | None, int]]:
    """Locate every token that closes a label.

    Returns [(label_end_index, last_label_word, inline_value_prefix, inline_conf,
    value_start_index)] where label_end_index is the index of the label's LAST
    word token and value_start_index is the first token after the colon."""
    out: list[tuple[int, str, str, float | None, int]] = []
    for i, t in enumerate(toks):
        txt = t.text
        if txt in _LONE_COLONS:
            # Tesseract split "Tele :" into two tokens: label is the previous word
            if i > 0 and _WORD_RE.match(toks[i - 1].text):
                out.append((i - 1, toks[i - 1].text, "", None, i + 1))
            continue
        if len(txt) > 1 and txt[0] in _COLONS and txt[1] not in _COLONS and i > 0 \
                and _WORD_RE.match(toks[i - 1].text):
            # Tesseract glued the colon to the value: "Number  :0082830253"
            out.append((i - 1, toks[i - 1].text, txt.lstrip(":：-— "), t.conf, i + 1))
            continue
        if txt.endswith(_COLONS):
            word = txt.rstrip(":：")
            if _WORD_RE.match(word) and word.lower() not in _URL_SCHEMES:
                out.append((i, word, "", None, i + 1))
            continue
        m = _INLINE_RE.match(txt)
        if m and m.group(1).lower() not in _URL_SCHEMES:
            out.append((i, m.group(1), m.group(2), t.conf, i + 1))
    return out


def _label_start(toks: list[_Tok], end: int, floor: int) -> int:
    """First token index of the label whose last word is toks[end]. `floor` is
    the first index this label may use (just past the previous label's colon).
    Labels are at most 3 words, or 5 when they begin the segment."""

    def walk(max_len: int) -> int:
        start = end
        while start - 1 >= floor and (end - start + 1) < max_len:
            if not _is_label_word(toks[start - 1].text):
                break
            start -= 1
        return start

    start = walk(5)
    if start != 0:  # label does not open the segment -> stricter length cap
        start = walk(3)
    # a label never opens with a bare connector ("of Birth:"), except "No ..."
    while start < end and toks[start].text.lower() in _CONNECTORS and toks[start].text.lower() != "no":
        start += 1
    return start


def _parse_segment(seg: _Segment) -> None:
    toks = seg.toks
    colons = _find_colons(toks)
    if not colons:
        return

    starts: list[int] = []
    floor = 0
    for end, _word, _prefix, _conf, _vs in colons:
        end = max(end, floor)
        starts.append(_label_start(toks, end, floor))
        floor = end + 1

    for k, (end, last_word, prefix, pconf, value_from) in enumerate(colons):
        start = starts[k]
        label_toks = toks[start:end + 1]
        label_text = " ".join([t.text for t in label_toks[:-1]] + [last_word])
        value_end = starts[k + 1] if k + 1 < len(colons) else len(toks)
        seg.pairs.append(_RawPair(
            label_toks=label_toks,
            label_text=label_text,
            value_toks=toks[value_from:value_end],
            inline_prefix=prefix or "",
            inline_conf=pconf,
        ))


def _valid_label(label: str) -> bool:
    letters = sum(c.isalpha() for c in label)
    if letters < 2 or len(label) > 40:
        return False
    if label.split()[0].lower() in _URL_SCHEMES:
        return False
    # a long all-lowercase "label" is the end of a sentence ("...initial commissioning:"),
    # not a printed field name; short ones ("unit", "stn") are real OCR lowercase
    if label == label.lower() and len(label) > 8:
        return False
    return True


def _value_text(value_toks: list[_Tok], prefix: str) -> str:
    parts = ([prefix] if prefix else []) + [t.text for t in value_toks]
    return _clean(" ".join(parts))


def _resolve_missing_value(
    seg: _Segment, pair: _RawPair, all_segs: list[_Segment]
) -> list[_Tok] | None:
    """A label with an empty value: take the segment to the right on the same
    row, else the one directly under it."""
    label_x2 = max(t.x2 for t in pair.label_toks)
    label_x1 = min(t.x1 for t in pair.label_toks)
    h = seg.height

    right: _Segment | None = None
    below: _Segment | None = None
    for other in all_segs:
        if other is seg:
            continue
        if other.pairs and other.toks[0] is other.pairs[0].label_toks[0]:
            continue  # that segment opens with a label of its own
        same_row = abs(other.cy - seg.cy) < 0.6 * h
        if same_row and other.x1 >= label_x2 - 5 and other.x1 - label_x2 < 12 * h:
            if right is None or other.x1 < right.x1:
                right = other
        elif 0 < other.y1 - seg.y2 + 0.2 * h < 2.6 * h and label_x1 - 2 * h <= other.x1 <= label_x1 + 6 * h:
            if below is None or other.y1 < below.y1:
                below = other
    chosen = right or below
    return chosen.toks if chosen else None


# ── pairs the form prints WITHOUT a colon ("Unit  14 JAK RIF", "Station   c/o 99 APO") ──────────────────────
_STOP_AFTER_LABEL = {"is", "are", "will", "was", "were", "has", "have", "had", "shall", "should", "be", "the", "a", "an",
                     "that", "which", "and", "for", "with", "as", "by", "on", "in", "at", "it", "this", "these", "may", "can",
                     "of", "to", "from", "into", "wef", "w.e.f", "taken", "report", "reg", "regarding"}
BARE_CONF_FACTOR = 0.92          # a pair without a colon is a little less certain than one with it
_ALIGN_TOL = 1.4                 # label column: left edges within this many text heights


def _norm_label(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _known_prefix(toks: list[_Tok], known: frozenset[str]) -> int:
    """Number of leading tokens that form a known printed label (longest first), 0 if none."""
    for n in range(min(4, len(toks)), 0, -1):
        if _norm_label(" ".join(t.text for t in toks[:n])) in known:
            return n
    return 0


def _bare_label_ok(label_toks: list[_Tok]) -> bool:
    first = label_toks[0].text
    return bool(first[:1].isalpha() and first[:1].isupper() and all(_is_label_word(t.text) for t in label_toks)
                and sum(c.isalpha() for t in label_toks for c in t.text) >= 2)


def _value_ok_bare(value_toks: list[_Tok], known: frozenset[str]) -> bool:
    if not value_toks:
        return False
    text = " ".join(t.text for t in value_toks)
    if not any(c.isalnum() for c in text):
        return False
    first = value_toks[0].text.strip(_EDGE_JUNK).lower()
    if first in _STOP_AFTER_LABEL:
        return False                                  # "Unit will be ...": a sentence, not a value
    if value_toks[-1].text.strip(_EDGE_JUNK).lower() in {"by", "to", "of", "for", "and", "no"} and len(value_toks) <= 4:
        return False                                  # "ISSUED BY  COLLECTED BY": the next label, not a value
    if _norm_label(text) in known:
        return False                                  # a label followed by another label has no value
    try:
        from app.knowledge.kb import value_is_header
        return not value_is_header(text)              # "Pers No | Rank & Name": the next column header
    except Exception:
        return True


def _make_pair(label_toks: list[_Tok], value_toks: list[_Tok], page_no: int) -> DiscoveredPair:
    confs = [t.conf for t in value_toks]
    conf = (sum(confs) / len(confs)) * BARE_CONF_FACTOR if confs else 0.0
    return DiscoveredPair(
        label=_clean(" ".join(t.text for t in label_toks)), value=_value_text(value_toks, ""),
        confidence=round(max(0.0, min(1.0, conf)), 3), page_number=page_no,
        bbox=_union(value_toks), label_bbox=_union(label_toks), kind="bare",
    )


def _bare_pairs(segs: list[_Segment], taken: list[DiscoveredPair], page_no: int, known: frozenset[str]) -> list[DiscoveredPair]:
    """Label/value pairs with no colon, found two ways (both need the form's own vocabulary or a label column):
      1. a segment that STARTS with a known printed label, the rest of the segment being the value;
      2. a short label segment with a value segment to its right on the same row (a wide gap instead of a colon),
         when it is a known label, or sits in a column of left-aligned labels of which at least two are known."""
    if not known:
        return []
    out: list[DiscoveredPair] = []
    used_ids: set[int] = set()

    def clashes(vtoks: list[_Tok], ltoks: list[_Tok]) -> bool:
        vb, lb = _union(vtoks), _union(ltoks)
        for p in list(taken) + out:
            for a, b in ((vb, p.bbox), (lb, p.label_bbox), (vb, p.label_bbox), (lb, p.bbox)):
                if a and b and min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]) > 0 and min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]) > 0:
                    return True
        return False

    cross: list[tuple[_Segment, _Segment, bool]] = []          # (label seg, value seg, label is known)
    for seg in segs:
        if seg.pairs:
            continue
        n = _known_prefix(seg.toks, known)
        if n and n < len(seg.toks):                            # case 1: "Unit 14 JAK RIF" in one segment
            lt, vt = seg.toks[:n], seg.toks[n:]
            if _bare_label_ok(lt) and _value_ok_bare(vt, known) and not clashes(vt, lt):
                out.append(_make_pair(lt, vt, page_no))
                used_ids.add(id(seg))
                continue
        if len(seg.toks) <= 4 and _bare_label_ok(seg.toks) and not any(c.isdigit() for t in seg.toks for c in t.text):
            right = [o for o in segs if o is not seg and abs(o.cy - seg.cy) < 0.6 * seg.height
                     and o.x1 >= seg.x2 - 5 and o.x1 - seg.x2 < 40 * seg.height and not o.pairs]
            if right:
                nxt = min(right, key=lambda o: o.x1)
                cross.append((seg, nxt, _norm_label(seg.text) in known))

    # case 2: known labels directly, then label columns
    columns: list[list[tuple[_Segment, _Segment, bool]]] = []
    for item in sorted(cross, key=lambda c: c[0].x1):
        for col in columns:
            if abs(col[0][0].x1 - item[0].x1) <= _ALIGN_TOL * item[0].height:
                col.append(item)
                break
        else:
            columns.append([item])
    for col in columns:
        n_known = sum(1 for _, _, k in col if k)
        for lab, val, is_known in col:
            if not (is_known or (len(col) >= 3 and n_known >= 2)):
                continue
            if id(lab) in used_ids or not _value_ok_bare(val.toks, known) or clashes(val.toks, lab.toks):
                continue
            out.append(_make_pair(lab.toks, val.toks, page_no))
            used_ids.add(id(lab))
    return _drop_table_columns(out)


def _drop_table_columns(pairs: list[DiscoveredPair]) -> list[DiscoveredPair]:
    """The same printed label three or more times, stacked in one column, is a table column ("Sub Depot | 2" on every
    row), not a form field: those pairs are dropped. A real form prints each label once per page (twice at most, for a
    left/right unit pair)."""
    groups: dict[str, list[DiscoveredPair]] = {}
    for p in pairs:
        groups.setdefault(_norm_label(p.label), []).append(p)
    drop: set[int] = set()
    for members in groups.values():
        if len(members) < 3:
            continue
        for m in members:
            lb = m.label_bbox or {}
            stacked = [o for o in members if o is not m and abs((o.label_bbox or {}).get("x1", 1e9) - lb.get("x1", -1e9)) <= 1.4 * max(1.0, lb.get("y2", 0) - lb.get("y1", 0))]
            if len(stacked) >= 2:
                drop.add(id(m))
    return [p for p in pairs if id(p) not in drop]


def discover_key_values(pages: list[list[OCRLine]], known_labels: frozenset[str] | None = None) -> list[DiscoveredPair]:
    """pages: OCRLine lists, one per page (page numbers are 1-based indexes). `known_labels` (normalised printed labels)
    switches on the colon-less pass; without it only "Label: value" pairs are returned, as before."""
    found: list[DiscoveredPair] = []

    for p_idx, lines in enumerate(pages):
        segs: list[_Segment] = []
        for ln in lines:
            segs.extend(_segments(ln))
        for seg in segs:
            _parse_segment(seg)

        page_pairs: list[DiscoveredPair] = []
        for seg in segs:
            for pair in seg.pairs:
                label = _clean(pair.label_text)
                if not _valid_label(label):
                    continue
                value_toks = pair.value_toks
                value = _value_text(value_toks, pair.inline_prefix)
                if not value:
                    resolved = _resolve_missing_value(seg, pair, segs)
                    if not resolved:
                        continue
                    value_toks = resolved
                    value = _value_text(value_toks, "")
                if not any(c.isalnum() for c in value):
                    continue
                confs = [t.conf for t in value_toks]
                if pair.inline_conf is not None and not value_toks:
                    confs = [pair.inline_conf]
                elif pair.inline_conf is not None:
                    confs.append(pair.inline_conf)
                conf = sum(confs) / len(confs) if confs else 0.0
                bbox_toks = value_toks if value_toks else pair.label_toks
                page_pairs.append(DiscoveredPair(
                    label=label,
                    value=value,
                    confidence=round(max(0.0, min(1.0, conf)), 3),
                    page_number=p_idx + 1,
                    bbox=_union(bbox_toks),
                    label_bbox=_union(pair.label_toks),
                ))
        if known_labels:
            page_pairs.extend(_bare_pairs(segs, page_pairs, p_idx + 1, known_labels))
        page_pairs.sort(key=lambda d: ((d.bbox or {}).get("y1", 0) // 12, (d.bbox or {}).get("x1", 0)))
        found.extend(page_pairs)
    return found


def slugify_label(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return slug or "field"


def assign_unique_keys(pairs: list[DiscoveredPair], taken: set[str]) -> list[tuple[str, DiscoveredPair]]:
    """Map each pair to a snake_case key that does not collide with `taken`
    (template fields) or with an earlier pair: ref, ref_2, ref_3 ..."""
    used = set(taken)
    out: list[tuple[str, DiscoveredPair]] = []
    for pair in pairs:
        base = slugify_label(pair.label)
        key, n = base, 1
        while key in used:
            n += 1
            key = f"{base}_{n}"
        used.add(key)
        out.append((key, pair))
    return out
