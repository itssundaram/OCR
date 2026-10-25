"""
Knowledge layer for the TMS / MMS / PSG forms: field vocabulary, left/right voucher columns, value shapes and
document-type landmarks, kept in plain JSON files (app/knowledge/data) so a change is a reviewable diff.

  enrich_fields(template_fields)  -> copies of the fields with printed-label aliases, a side hint, a soft value
                                     pattern and `kb_id` added where the field is a known one. Never overrides
                                     what the template already says, never drops a field.
  zone_ok(fdef, label_tokens, x_frac) -> False when a label that is printed in BOTH columns of a voucher
                                     ("Unit") is being taken from the wrong half of the page.
  classify_page(text)             -> (doc_type, score) from weighted landmark phrases, or ("", score).

Pure functions; the JSON is read once.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data"
MAX_ALIASES = 14
CLASSIFY_MIN_SCORE = 0.35      # share of a type's landmark weight that must be present
CLASSIFY_MARGIN = 0.08         # and it must beat the runner-up by this much
ZONE_SPLIT = 0.5               # a two-column voucher: left labels start before this fraction of the page width
RIGHT_VALUE_MIN = 0.42         # a right-column value starts at or after this (values sit a little right of their label)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


@lru_cache(maxsize=1)
def _fields() -> dict:
    data = json.loads((DATA / "fields.json").read_text(encoding="utf-8"))
    by_name: dict[str, dict] = {}
    for f in data["fields"]:
        for n in f.get("names", []):
            by_name.setdefault(_slug(n), f)
    return {"by_name": by_name, "all": data["fields"]}


@lru_cache(maxsize=1)
def _synonyms_file() -> dict:
    try:
        return json.loads((DATA / "label_synonyms.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def label_synonym_groups() -> list[list[str]]:
    """Groups of printed labels naming one concept (label_synonyms.json). Independent of KNOWLEDGE_ENABLED:
    this is general label matching, not the voucher knowledge."""
    return [[str(p) for p in g] for g in _synonyms_file().get("groups", []) if isinstance(g, list)]


def spelling_variants() -> dict[str, str]:
    """variant -> canonical token, applied to labels before they are compared (label_synonyms.json)."""
    return {str(k): str(v) for k, v in (_synonyms_file().get("spellings") or {}).items() if not str(k).startswith("_")}


@lru_cache(maxsize=1)
def _types() -> list[dict]:
    return json.loads((DATA / "doc_types.json").read_text(encoding="utf-8"))["types"]


def _on() -> bool:
    try:
        from app.core.config import settings
        return bool(getattr(settings, "KNOWLEDGE_ENABLED", True))
    except Exception:
        return True


def known_field(fdef: dict) -> dict | None:
    """The knowledge entry for a template field, found by its name (or label / display name)."""
    idx = _fields()["by_name"]
    for key in ("name", "label", "display_name"):
        hit = idx.get(_slug(fdef.get(key)))
        if hit:
            return hit
    return None


def enrich_fields(template_fields: list[dict]) -> list[dict]:
    if not _on():
        return list(template_fields or [])
    out: list[dict] = []
    for fdef in template_fields or []:
        kb = known_field(fdef) if isinstance(fdef, dict) and str(fdef.get("type") or "").lower() != "table" else None
        if not kb:
            out.append(fdef)
            continue
        e = dict(fdef)
        e["kb_id"] = kb["id"]
        aliases = list(e.get("aliases") or [])
        have = {_slug(a) for a in aliases} | {_slug(e.get(k)) for k in ("name", "label", "display_name")}
        for lab in kb.get("labels", []):
            if _slug(lab) not in have and len(aliases) < MAX_ALIASES:
                aliases.append(lab)
                have.add(_slug(lab))
        e["aliases"] = aliases
        if kb.get("exact_labels") and "exact_labels" not in e:
            e["exact_labels"] = True
        if kb.get("side") and not e.get("side"):
            e["side"] = kb["side"]
            e["strict_labels"] = list(kb.get("strict_labels") or [])
        if kb.get("min_digits"):
            e["min_digits"] = int(kb["min_digits"])
        if kb.get("needs_digit"):
            e["needs_digit"] = True
        if kb.get("share"):
            e["share"] = kb["share"]
        if kb.get("pattern") and not (e.get("pattern") or e.get("regex")):
            e["pattern"] = kb["pattern"]
        if kb.get("kind") == "date" and not e.get("type"):
            e["type"] = "date"
        out.append(e)
    return out


def zone_ok(fdef: dict, label_tokens: list[str], label_frac: float | None, value_frac: float | None = None) -> bool:
    """Left / right column check for a field that has a side (the Unit on the left or right of a voucher).
    Label: a label that BOTH columns print ("Unit") may only be used from the field's own half of the page.
    Value: a value that starts in the other half is never taken (a hidden left Unit must not be filled with the
    right column's text). Other labels ("Issued To") are not restricted by the label rule; fields without a
    side are never restricted. Fractions are x / page width."""
    side = fdef.get("side")
    if side not in ("left", "right"):
        return True
    if value_frac is not None:
        if side == "left" and value_frac >= ZONE_SPLIT:
            return False
        if side == "right" and value_frac < RIGHT_VALUE_MIN:
            return False
    strict = fdef.get("strict_labels") or []
    if strict and label_frac is not None and "".join(label_tokens) in {"".join(_words(x)) for x in strict}:
        return label_frac < ZONE_SPLIT if side == "left" else label_frac >= ZONE_SPLIT
    return True


def generic_label(fdef: dict, label_tokens: list[str]) -> bool:
    """True when the label is one of the field's strict (both-columns) labels, e.g. a bare "Unit". Such a label is
    weaker evidence than a specific one (Consignor, Issued To) for the same field: on a single-column voucher the
    "Unit" in the collector's signature block is not the issuing unit."""
    strict = fdef.get("strict_labels") or []
    return bool(strict) and "".join(label_tokens) in {"".join(_words(x)) for x in strict}


def _present(phrase_words: list[str], page_words: set[str]) -> bool:
    for w in phrase_words:
        if w in page_words:
            continue
        if len(w) >= 5 and any(abs(len(p) - len(w)) <= 1 and _close(p, w) for p in page_words):   # one OCR typo
            continue
        return False
    return True


def _close(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def norm_label(text: str) -> str:
    """Lower-case words joined by one space ("Veh BA No." -> "veh ba no"), the form labels are compared in."""
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


@lru_cache(maxsize=1)
def known_labels() -> frozenset[str]:
    """Every label the forms print: the canonical field labels plus the extra label lexicon. Used to find
    "Label value" pairs on forms that print no colon. Empty when the knowledge layer is switched off."""
    if not _on():
        return frozenset()
    out: set[str] = set()
    for f in _fields()["all"]:
        for lab in f.get("labels") or []:
            k = norm_label(lab)
            if k:
                out.add(k)
    try:
        d = json.loads((DATA / "lexicons.json").read_text(encoding="utf-8"))
        out.update(k for k in (norm_label(x) for x in d.get("labels", [])) if k)
    except Exception:
        pass
    return frozenset(out)


def classify_page(text: str) -> tuple[str, float, list[tuple[str, float]]]:
    """(doc_type or "", score of the winner, all scores best-first). Landmarks are phrases; a phrase counts
    when all its words are on the page (one-letter OCR typos tolerated on longer words)."""
    page_words = set(_words(text))
    if not page_words:
        return "", 0.0, []
    scores: list[tuple[str, float]] = []
    family = {t["id"]: t.get("family", "") for t in _types()}
    for t in _types():
        total = sum(w for _, w in t["landmarks"])
        got = sum(w for ph, w in t["landmarks"] if _present(_words(ph), page_words))
        scores.append((t["id"], round(got / total, 3) if total else 0.0))
    scores.sort(key=lambda s: -s[1])
    best = scores[0]
    # the same form family often shares landmarks between its own types, so only another family's score is a rival
    runner = max((s for i, s in scores[1:] if family.get(i) != family.get(best[0])), default=0.0)
    if best[1] >= CLASSIFY_MIN_SCORE and best[1] - runner >= CLASSIFY_MARGIN:
        return best[0], best[1], scores
    return "", best[1], scores


# ── table headers ─────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _headers() -> dict:
    data = json.loads((DATA / "table_headers.json").read_text(encoding="utf-8"))
    index: dict[str, dict] = {}
    for h in data["headers"]:
        for v in [h["label"], *h["variants"]]:
            index.setdefault(_hkey(v), h)
    return {"index": index, "split": set(data.get("split_terms", [])), "all": data["headers"]}


def _hkey(text: str) -> str:
    """Comparison key: letters and digits only, O/0 and I/l/1 folded (what OCR confuses in short headers)."""
    t = re.sub(r"[^a-z0-9]", "", str(text or "").lower())
    return t.replace("0", "o").replace("1", "l").replace("i", "l")


def _strip_header(text: str) -> str:
    t = re.sub(r"\(.*?\)|\(.*$", " ", str(text or ""))        # "DOR (Capt)" -> "DOR"
    return re.sub(r"\s+", " ", t).strip(" \t-_~=.,:;|/'\"`*!\\^+<>")


def canonical_header(text: str) -> dict | None:
    """The knowledge entry for a printed column header, or None. Short keys (3 letters or fewer) must match
    exactly; longer ones tolerate one typo."""
    key = _hkey(_strip_header(text))
    if not key:
        return None
    idx = _headers()["index"]
    if key in idx:
        return idx[key]
    if len(key) >= 5:
        from difflib import get_close_matches
        near = get_close_matches(key, list(idx), n=1, cutoff=0.88)
        if near:
            return idx[near[0]]
    return None


def value_is_header(text: str) -> bool:
    """True when a "value" is really other column header(s) ("DOS", "DOB /Unit"): a label found in a table's
    header row must not be given the next header as its value."""
    t = str(text or "").strip()
    if not t or len(t) > 40:
        return False
    toks = [w for w in re.findall(r"[A-Za-z]{2,}", t)]
    if not toks or len(toks) > 5:
        return False
    return all(canonical_header(w) is not None for w in toks)


def _generic(text: str) -> bool:
    return not text or bool(re.fullmatch(r"column\s*\d+", str(text).strip(), re.I))


def _set(entry: dict, h: dict, raw: str, fused: bool = False) -> None:
    """Record the canonical id. The printed text is only replaced when it is noisy or fused; a clean printed
    header ("Ser", "Regd Nos") stays as printed."""
    entry.update(id=h["id"], kind=h.get("kind", ""))
    cleaned = _strip_header(raw)
    exact = _hkey(cleaned) in {_hkey(v) for v in [h["label"], *h["variants"]]}
    if fused or not exact or re.search(r"[^A-Za-z0-9 &/.]", cleaned):
        entry["text"] = h["label"]
    else:
        entry["text"] = cleaned


def normalize_headers(headers: list[str]) -> list[dict]:
    """[{text, id, kind, raw}] per column. Known headers get their canonical label; a cell that fuses several
    date abbreviations ("DOC))DOS") is spread over itself and the generic "Column N" cells right after it.
    Anything unknown keeps its text, so nothing is lost."""
    if not _on():
        return [{"text": h, "id": "", "kind": "", "raw": h} for h in headers]
    out = [{"text": h, "id": "", "kind": "", "raw": h} for h in headers]
    split = _headers()["split"]
    i = 0
    while i < len(headers):
        terms = [w for w in re.findall(r"[a-z0-9]+", _strip_header(headers[i]).lower()) if _hkey(w) in {_hkey(t) for t in split}]
        if len(terms) >= 2:
            slots = [i]
            j = i + 1
            while j < len(headers) and _generic(headers[j]) and len(slots) < len(terms):
                slots.append(j)
                j += 1
            if len(slots) == len(terms):                       # exactly enough room: one term per cell
                for slot, term in zip(slots, terms):
                    h = canonical_header(term)
                    if h:
                        _set(out[slot], h, term, fused=True)
                i = j
                continue
        h = canonical_header(headers[i])
        if h:
            _set(out[i], h, headers[i])
        i += 1
    return out


# ── table records ─────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _lex() -> dict:
    d = json.loads((DATA / "lexicons.json").read_text(encoding="utf-8"))
    ranks = sorted(d["ranks"], key=lambda r: -len(r))          # longest first: "Lt Col" before "Lt"
    return {"ranks": ranks, "genders": d["genders"]}


_PERS = re.compile(r"^\s*([A-Za-z]{1,3}\s?-?\s?\d{4,7}\s?[A-Za-z])\b\s*(.*)$")
_SUS_TAIL = re.compile(r"(\d{5,8}\s?[A-Za-z])\s*$")


def _spaced(unit: str) -> str:
    """'303FdHosp' -> '303 Fd Hosp' (OCR drops the gaps of a tightly set cell)."""
    if " " in unit.strip():
        return re.sub(r"\s+", " ", unit.strip())
    return re.sub(r"(?<=\d)(?=[A-Za-z])|(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)", " ", unit.strip())


def _split_pers_rank_name(text: str) -> dict:
    m = _PERS.match(text or "")
    if not m:
        return {"rank_name": (text or "").strip()}
    pers, rest = re.sub(r"\s+", "", m.group(1)).upper(), m.group(2).strip()
    rank = ""
    for r in _lex()["ranks"]:
        if re.match(rf"^{re.escape(r)}\b", rest, re.I):
            rank, rest = r, rest[len(r):].strip()
            break
    if not rank:
        rank, rest = _snap_rank(rest)
    return {"pers_no": pers, "rank": rank, "name": rest}


def _snap_rank(rest: str) -> tuple[str, str]:
    """A rank that OCR misread by one letter ("Cant" -> "Capt") is taken from the closed rank list. Only ranks of four
    letters or more are snapped (a one-letter slip in "Lt" or "Col" is too ambiguous) and a name must remain."""
    words = rest.split()
    for r in _lex()["ranks"]:
        n = len(r.split())
        if len(r.replace(" ", "")) < 4 or len(words) <= n:
            continue
        got = " ".join(words[:n])
        if got.lower() != r.lower() and _close(got.lower().replace(" ", ""), r.lower().replace(" ", "")):
            return r, " ".join(words[n:])
    return "", rest


def _split_unit_sus(text: str) -> dict:
    t = (text or "").strip()
    m = _SUS_TAIL.search(t)
    if not m:
        return {"unit": _spaced(t.strip(" &")) if t else ""}
    unit = t[:m.start()].strip(" &,-")
    return {"unit": _spaced(unit), "sus_no": re.sub(r"\s+", "", m.group(1)).upper()}


def _date_text(text: str) -> str:
    """dd-mm-yyyy from a noisy numeric date ("O1-.\u00b0 09- 2025", "02-12 2029.", "02-122029"); text is returned
    unchanged when it does not hold a plausible day, month and year."""
    from datetime import date
    raw = str(text or "").strip()
    t = raw.translate(str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"}))
    g = re.findall(r"\d+", t)
    if len(g) == 2 and len(g[1]) in (6, 8):                      # day, then month and year run together
        g = [g[0], g[1][:2], g[1][2:]]
    elif len(g) == 1 and len(g[0]) in (6, 8):
        g = [g[0][:2], g[0][2:4], g[0][4:]]
    if len(g) == 3 and len(g[2]) in (2, 4):
        try:
            y = int(g[2]) + (2000 if len(g[2]) == 2 else 0)
            return date(y, int(g[1]), int(g[0])).strftime("%d-%m-%Y")
        except ValueError:
            pass
    return raw


def table_records(known: list[dict], rows: list[list[str]]) -> list[dict]:
    """One dict per row keyed by canonical column id, with fused columns split ("Unit & SUS No" -> unit + sus_no,
    "Pers No Rank & Name" -> pers_no + rank + name) and dates written dd-mm-yyyy. Cells that cannot be parsed
    are passed through unchanged. Empty when fewer than three columns are recognised."""
    if sum(1 for k in known if k.get("id")) < 3:
        return []
    out: list[dict] = []
    genders = _lex()["genders"]
    for row in rows:
        rec: dict = {}
        for k, cell in zip(known, row):
            cid, val = k.get("id"), (cell or "").strip()
            if not cid:
                continue
            if cid == "pers_rank_name":
                rec.update(_split_pers_rank_name(val))
            elif cid == "unit_sus":
                rec.update(_split_unit_sus(val))
            elif k.get("kind") == "date":
                rec[cid] = _date_text(val)
            elif cid == "gender":
                rec[cid] = genders.get(re.sub(r"[^a-z]", "", val.lower()), val)
            else:
                rec[cid] = val
        out.append(rec)
    return out
