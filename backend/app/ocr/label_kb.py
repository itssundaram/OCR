"""
Label knowledge base: different printed labels that mean the same field.

A template field called "unit" is found on a form that prints "From"; "BA No" is printed as "Veh BA No";
"Dated" as "Date of issue". Matching only by spelling cannot know that. This module holds groups of labels
that name the same concept and answers "do these two labels name the same thing?".

Three sources, all optional and all merged:
  1. SEED_GROUPS below - general business / form vocabulary plus the services-logistics forms we read.
  2. settings.LABEL_SYNONYMS_FILE - a JSON file (list of lists of phrases) an operator can extend without code.
  3. per-template aliases on the field itself (template_json fields[].aliases). Those are exact matches in
     kv_match; they are also LEARNED automatically when a person locks a field whose printed label differs
     from the field name (see learn_aliases_from_anchors), so each correction teaches the template.

Matching is deliberately conservative: a synonym match scores below an exact or typo match, so a page that
prints both "Unit" and "From" gives "Unit" to the field called unit and "From" to the field called from.
Pure functions, no I/O except reading the optional file once.
"""
from __future__ import annotations

import json
import os
import re
import threading

from app.core.logging import get_logger

logger = get_logger(__name__)

SYNONYM_SCORE = 0.88      # below exact (1.0) and typo-tolerant (0.9) matches, above the loose subset match (0.86)

# Each group = phrases that name one concept. Written as people print them; normalised the same way as labels.
SEED_GROUPS: list[list[str]] = [
    # who / from / to
    ["from", "unit", "issued by", "issuing unit", "consignor", "sender", "despatched by", "dispatched by",
     "originator", "raised by", "submitted by", "forwarded by"],
    ["to", "issued to", "consignee", "consigned to", "consign to", "receiver", "recipient", "addressee",
     "despatched to", "dispatched to", "forwarded to", "submitted to"],
    ["name", "full name", "name of person", "person name", "name of individual", "candidate name", "employee name",
     "name of officer", "name of the person"],
    ["father name", "father s name", "name of father", "fathers name", "s o", "son of"],
    ["rank", "designation", "grade"],
    ["address", "addr", "postal address", "mailing address", "residential address", "permanent address"],
    ["place", "location", "station", "stn", "loc"],
    ["phone", "tel", "telephone", "mobile", "mob", "mobile no", "contact no", "ph no", "phone no", "tele no"],
    ["email", "e mail", "mail id", "email id", "e mail id", "email address"],
    # dates
    ["date", "dated", "dt", "issue date", "date of issue", "date issued", "doc date", "document date", "dtd"],
    ["dob", "date of birth", "birth date", "born on"],
    ["due date", "date due", "payable by", "last date"],
    # numbers / references
    ["ref no", "ref", "reference no", "reference", "our ref", "your ref", "file no", "letter no", "ref number"],
    ["sl no", "s no", "sr no", "serial no", "serial", "ser no", "ser", "sno"],
    ["inv no", "invoice no", "bill no", "invoice number"],
    ["po no", "p o no", "purchase order no", "purchase order", "order no"],
    ["batch no", "batch number", "lot no"],
    ["id no", "identity no", "identification no", "card no"],
    ["gst no", "gstin", "gst in", "gst number", "gst"],
    ["pan", "pan no", "pan number", "permanent account number"],
    ["aadhaar no", "aadhar no", "aadhaar", "aadhar", "uid", "uidai"],
    # money / quantity
    ["amount", "amt", "total amount", "grand total", "net amount", "net payable", "amount payable", "total value"],
    ["qty", "quantity", "qnty", "no of units"],
    ["rate", "unit price", "unit rate"],
    ["description", "desc", "particulars", "item description", "details", "nomenclature"],
    ["remarks", "remark", "comments", "comment", "note", "notes"],
    ["authority", "auth", "authorisation", "authorization", "order authority"],
    # vehicle / stores / services-logistics forms
    ["ba no", "veh ba no", "army no", "ba number", "ba reg no"],
    ["veh no", "vehicle no", "reg no", "registration no", "vehicle registration no", "regn no"],
    ["chasis no", "chassis no", "frame no", "vin"],
    ["eng no", "engine no", "motor no"],
    ["rv no", "receipt voucher no", "receipt voucher", "rv number"],
    ["iv no", "issue voucher no", "issue voucher", "iv number"],
    ["type of str", "type of store", "type of stores", "store type", "str type", "category of store"],
    ["cat part no", "cat no", "part no", "catalogue no", "part number", "item code", "stock no", "nsn"],
    ["a u", "au", "unit of issue", "uom", "unit of measure"],
]


def _norm(text: str) -> tuple[str, ...]:
    """Same normalisation kv_match applies to labels (imported lazily: kv_match imports this module)."""
    from app.ocr.kv_match import _tokens
    toks = _tokens(text)
    return tuple(t for t in toks if t)


_lock = threading.Lock()
_index: dict[tuple[str, ...], set[int]] | None = None
_stamp: tuple = ()


def _file_groups() -> tuple[list[list[str]], tuple]:
    try:
        from app.core.config import settings
        path = getattr(settings, "LABEL_SYNONYMS_FILE", None)
    except Exception:
        path = None
    if not path or not os.path.isfile(path):
        return [], ("nofile",)
    try:
        stamp = (path, os.path.getmtime(path))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        groups = [[str(p) for p in g] for g in data if isinstance(g, list)]
        return groups, stamp
    except Exception as exc:
        logger.warning("label_synonyms_file_unreadable", path=path, error=str(exc))
        return [], ("bad", path)


def _build() -> dict[tuple[str, ...], set[int]]:
    global _stamp
    extra, stamp = _file_groups()
    _stamp = stamp
    index: dict[tuple[str, ...], set[int]] = {}
    for gid, group in enumerate([*SEED_GROUPS, *extra]):
        for phrase in group:
            key = _norm(phrase)
            if key:
                index.setdefault(key, set()).add(gid)
    return index


def _get_index() -> dict[tuple[str, ...], set[int]]:
    global _index
    with _lock:
        extra_stamp = _file_groups()[1] if _index is not None else None
        if _index is None or extra_stamp != _stamp:
            _index = _build()
        return _index


def reload() -> None:
    global _index
    with _lock:
        _index = None


def groups_of(tokens: list[str] | tuple[str, ...]) -> set[int]:
    return _get_index().get(tuple(tokens), set())


def same_concept(a: list[str], b: list[str]) -> bool:
    """True when two (already normalised) labels sit in the same synonym group but are not identical."""
    if not a or not b or tuple(a) == tuple(b):
        return False
    ga, gb = groups_of(a), groups_of(b)
    return bool(ga and gb and (ga & gb))


def synonyms_of(tokens: list[str]) -> list[list[str]]:
    """Every other phrase in the groups this label belongs to (for label-without-colon search)."""
    idx = _get_index()
    gids = idx.get(tuple(tokens), set())
    if not gids:
        return []
    out: list[list[str]] = []
    seen = {tuple(tokens)}
    for key, groups in idx.items():
        if key not in seen and groups & gids:
            seen.add(key)
            out.append(list(key))
    return out


# ── learning ────────────────────────────────────────────────────────────────────────────
def _clean_alias(text: str) -> str:
    t = re.sub(r"[\s:;=\-–—|]+$", "", (text or "").strip())
    t = re.sub(r"^[\s:;=\-–—|]+", "", t)
    return re.sub(r"\s+", " ", t)


def learn_aliases_from_anchors(template_fields: list[dict], anchors_fields: dict, only: list[str] | None = None,
                               max_aliases: int = 12) -> list[dict]:
    """After a person confirms where a field is, the label printed next to it (anchor.label_text) is a proven
    name for that field. Add it to the field's aliases when the field's own names would not already match it.
    Returns [{field, alias}] for what was added; `template_fields` is edited in place."""
    from app.ocr.kv_match import MIN_SCORE, _candidates, _score, _tokens
    added: list[dict] = []
    for fdef in template_fields or []:
        name = fdef.get("name")
        if not name or (only is not None and name not in only):
            continue
        label = _clean_alias(str((anchors_fields.get(name) or {}).get("label_text") or ""))
        if not label or len(label) > 60 or not re.search(r"[A-Za-z]", label):
            continue
        toks = _tokens(label)
        if not toks:
            continue
        already = [list(c) for c in _candidates(fdef)]
        if any(_score(c, toks) >= MIN_SCORE or same_concept(c, toks) for c in already):
            continue                                        # the field already answers to this label
        aliases = list(fdef.get("aliases") or [])
        if len(aliases) >= max_aliases or any(_tokens(a) == toks for a in aliases):
            continue
        aliases.append(label)
        fdef["aliases"] = aliases
        added.append({"field": name, "alias": label})
    return added
