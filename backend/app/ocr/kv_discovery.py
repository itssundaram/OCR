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


def discover_key_values(pages: list[list[OCRLine]]) -> list[DiscoveredPair]:
    """pages: OCRLine lists, one per page (page numbers are 1-based indexes)."""
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
