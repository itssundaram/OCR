"""
DOCINT — match template fields to discovered key : value pairs
==============================================================
The page is scanned once for every "Label: value" pair (app/ocr/kv_discovery.py).
This module then answers "which of those pairs is my template field?" by
comparing the template field's name / label / aliases with each pair's printed
label. No model, no extra OCR: pure string work, microseconds per document.

Pure functions, no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.knowledge.kb import generic_label, spelling_variants, value_is_header, zone_ok
from app.ocr import label_kb
from app.ocr.kv_discovery import DiscoveredPair

MIN_SCORE = 0.85  # below this a label is not considered the same field

def _canon() -> dict[str, str]:
    """Variant spellings folded to one token (app/knowledge/data/label_synonyms.json, editable data)."""
    return spelling_variants()




_SIDE_WORDS = {"left", "right", "side", "hand", "of", "the", "page"}
# a trailing qualifier on a field name ("unit_from", "address_to"): the printed label is usually just "Unit" / "Address"
_DIRECTION_WORDS = {"from": "left", "to": "right"}


@dataclass
class KVMatch:
    field_name: str
    pair: DiscoveredPair
    score: float


def _tokens(text: str) -> list[str]:
    text = re.sub(r"\(.*?\)", " ", text or "")           # "UNIT (right side)" -> "UNIT"
    words = re.findall(r"[a-z0-9]+", text.lower().replace("_", " "))
    canon = _canon()
    return [canon.get(w, w) for w in words]


def _edit_distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, giving up (returning limit + 1) once it must exceed `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _score(cand: list[str], label: list[str], use_synonyms: bool = True) -> float:
    if not cand or not label:
        return 0.0
    a, b = "".join(cand), "".join(label)
    if a == b:
        return 1.0
    # tolerate OCR typos only: one wrong character (two on long labels), never
    # a different word ("consign to" must not match "consignor")
    allowed = 1 if max(len(a), len(b)) <= 12 else 2
    best = 0.9 if _edit_distance(a, b, allowed) <= allowed else 0.0
    short, long_ = (cand, label) if len(cand) <= len(label) else (label, cand)
    if len(short) >= 2 and set(short) <= set(long_):
        best = max(best, 0.86)
    if use_synonyms and best < label_kb.SYNONYM_SCORE and label_kb.same_concept(cand, label):
        best = label_kb.SYNONYM_SCORE            # "unit" == "from", "Dated" == "Date of issue" (see label_kb)
    return best


def _candidates(fdef: dict) -> list[list[str]]:
    raw = [fdef.get("name"), fdef.get("label"), fdef.get("display_name")]
    raw += list(fdef.get("aliases") or [])
    seen, out = set(), []
    for r in raw:
        toks = _tokens(str(r)) if r else []
        if "left" in toks or "right" in toks:  # "unit_right_side_of_the_page" -> "unit"
            toks = [t for t in toks if t not in _SIDE_WORDS]
        key = tuple(toks)
        if toks and key not in seen:
            seen.add(key)
            out.append(toks)
        # "unit_from" is printed as plain "Unit" on the form: the name without its from/to qualifier is a candidate too
        if len(toks) >= 2 and toks[-1] in _DIRECTION_WORDS:
            base = tuple(toks[:-1])
            if base not in seen:
                seen.add(base)
                out.append(list(base))
    return out


def _side_hint(fdef: dict) -> str:
    if fdef.get("side") in ("left", "right"):                 # set by the knowledge layer (app/knowledge)
        return fdef["side"]
    text = " ".join(str(fdef.get(k) or "") for k in ("name", "label", "display_name")).lower()
    if "left" in text:
        return "left"
    if "right" in text:
        return "right"
    for name in (fdef.get("name"), fdef.get("label"), fdef.get("display_name")):
        toks = _tokens(str(name)) if name else []
        if len(toks) >= 2 and toks[-1] in _DIRECTION_WORDS:
            return _DIRECTION_WORDS[toks[-1]]
    return ""


def _value_ok(fdef: dict, value: str) -> bool:
    ftype = str(fdef.get("type") or "").lower()
    # the field's own shape (knowledge layer `extract` / `blacklist`): "KM" is not a chassis number, "Adequate" is not an engine number
    rx = fdef.get("kb_extract")
    if rx:
        try:
            if not re.search(rx, value):
                return False
        except re.error:
            pass
    low = value.lower()
    if any(str(b).lower() in low for b in (fdef.get("kb_blacklist") or [])):
        return False
    if fdef.get("needs_digit"):
        return sum(c.isdigit() for c in value) >= int(fdef.get("min_digits") or 1)
    if ftype in ("date", "datetime") or "date" in _tokens(str(fdef.get("name") or "")):
        return any(c.isdigit() for c in value)
    return True


def _x_fracs(pair: DiscoveredPair, page_widths: dict[int, int] | None) -> tuple[float | None, float | None]:
    """(label x, value x) as fractions of the page width."""
    w = (page_widths or {}).get(getattr(pair, "page_number", 0))
    if not w:
        return None, None
    lx = (pair.label_bbox or {}).get("x1")
    vx = (pair.bbox or {}).get("x1")
    return (lx / w if lx is not None else None), (vx / w if vx is not None else None)


GENERIC_LABEL_FACTOR = 0.97
SHARE_MIN_SCORE = 0.99


def match_fields(template_fields: list[dict], pairs: list[DiscoveredPair],
                 page_widths: dict[int, int] | None = None) -> dict[str, KVMatch]:
    """Return {field_name: KVMatch} for every non-table template field that has a
    pair with a matching label. Each pair is used by at most one field. `page_widths` ({page: px}) lets a field
    with a left/right side refuse a label that both voucher columns print ("Unit") from the wrong half."""
    scored: list[tuple[float, int, int]] = []   # (score, field index, pair index)
    fields = [f for f in (template_fields or []) if f.get("name") and str(f.get("type") or "").lower() != "table"]
    pair_tokens = [_tokens(p.label) for p in pairs]

    for fi, fdef in enumerate(fields):
        cands = _candidates(fdef)
        for pi, pair in enumerate(pairs):
            if not _value_ok(fdef, pair.value) or value_is_header(pair.value):
                continue
            lf, vf = _x_fracs(pair, page_widths)
            if not zone_ok(fdef, pair_tokens[pi], lf if lf is not None else vf, vf):
                continue
            syn = not fdef.get("exact_labels")
            s = max((_score(c, pair_tokens[pi], syn) for c in cands), default=0.0)
            if s >= MIN_SCORE and generic_label(fdef, pair_tokens[pi]):
                s *= GENERIC_LABEL_FACTOR      # a bare "Unit" ranks below a specific label (Consignor) for the same field
            if s >= MIN_SCORE:
                scored.append((s, fi, pi))

    out: dict[str, KVMatch] = {}
    used: set[int] = set()
    shared: dict[int, str] = {}      # pair index -> share group of the field that took it with an exact label
    # best score first; among equal scores the pair higher on the page wins
    # (or the left-/right-most one when the field name says so)
    def order(item):
        s, fi, pi = item
        bb = pairs[pi].bbox or {}
        hint = _side_hint(fields[fi])
        x = bb.get("x1", 0)
        pos = -x if hint == "right" else x if hint == "left" else 0
        # equal label score: a clearly better read wins over an earlier page (the same form can repeat on page 2)
        return (-s, pos, -int(pairs[pi].confidence * 5), pairs[pi].page_number, bb.get("y1", 0), bb.get("x1", 0))

    for s, fi, pi in sorted(scored, key=order):
        name = fields[fi]["name"]
        if name in out:
            continue
        # one pair feeds one field, except fields of one share group (see knowledge/data/fields.json) that both print this exact specific label
        # (From/Depot, Consignor and the issuing Unit are the same printed line on an issue voucher)
        tag = fields[fi].get("share")
        if pi in used and not (tag and s >= SHARE_MIN_SCORE and shared.get(pi) == tag):
            continue
        out[name] = KVMatch(field_name=name, pair=pairs[pi], score=s)
        if pi not in used and tag and s >= SHARE_MIN_SCORE:
            shared[pi] = tag
        used.add(pi)
    return out
