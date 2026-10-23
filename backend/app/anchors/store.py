"""
DOCINT - merge-safe, versioned anchor store.

Used by BOTH save paths (lock-anchors and manage-template-field) so they can
never disagree about what a stored anchor looks like, and neither one can
silently throw away fields the other one saved.

Rules:
  * incoming fields are MERGED into the existing ones (a field missing from the
    request is kept; removal is an explicit action);
  * every saved field carries page_number, a page-relative box, the printed
    label found next to it and the confirmed value's shape - everything the
    resolver needs to read it back on the next run;
  * the anchor set has an integer ``version``; a caller can pass the version it
    last saw and gets AnchorConflict if someone saved in between;
  * the previous version is pushed onto a short history so a bad save can be
    rolled back.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from app.anchors.resolver import (
    box_ok, find_label_near, to_relative, value_shape,
)

SCHEMA_VERSION = 2
HISTORY_LIMIT = 10


class AnchorConflict(Exception):
    def __init__(self, current: int, expected: int):
        super().__init__(f"anchors changed (current version {current}, caller had {expected})")
        self.current, self.expected = current, expected


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_field_anchor(
    raw: dict,
    page_w: float,
    page_h: float,
    words_for_page: list[dict] | None = None,
    previous: dict | None = None,
) -> dict:
    """Normalise one incoming field into the v2 shape, enriching it from OCR words if given."""
    out = dict(previous or {})
    out.update({k: v for k, v in (raw or {}).items() if v is not None})
    bb = out.get("bbox")
    if not box_ok(bb):
        return out  # nothing geometric to enrich (e.g. a not-found field kept for its name)
    page_w = float(out.get("page_width") or page_w or 1654)
    page_h = float(out.get("page_height") or page_h or 2338)
    out["page_number"] = int(out.get("page_number") or 1)
    out["page_width"], out["page_height"] = int(page_w), int(page_h)
    out["rel_bbox"] = to_relative(bb, page_w, page_h)

    if words_for_page:
        lab = find_label_near(words_for_page, bb, page_w)
        if lab:
            out["label_text"] = lab["text"]
            out["label_rel_bbox"] = to_relative(lab["bbox"], page_w, page_h)
        if not (out.get("value") or "").strip():
            from app.anchors.resolver import resolve_anchor
            rd = resolve_anchor(out, [words_for_page], [(page_w, page_h)])
            if rd and rd.valid:
                out["value"] = rd.value
    if (out.get("value") or "").strip():
        out["shape"] = value_shape(out["value"])
    out["schema"] = SCHEMA_VERSION
    return out


def merge_anchor_fields(
    existing: dict | None,
    incoming: dict[str, dict],
    *,
    page_w: float,
    page_h: float,
    verified: bool | None = None,
    words_by_page: list[list[dict]] | None = None,
    expected_version: int | None = None,
    source: str = "user",
) -> tuple[dict, dict]:
    """Return (new learned_anchors dict, report)."""
    existing = copy.deepcopy(existing or {})
    cur_v = int(existing.get("version") or 0)
    if expected_version is not None and int(expected_version) != cur_v:
        raise AnchorConflict(cur_v, int(expected_version))

    old_fields = dict(existing.get("fields") or {})
    new_fields = dict(old_fields)
    added, updated = [], []
    for name, raw in (incoming or {}).items():
        if not isinstance(raw, dict):
            continue
        prev = old_fields.get(name)
        pn = int(raw.get("page_number") or (prev or {}).get("page_number") or 1)
        words = None
        if words_by_page and 0 <= pn - 1 < len(words_by_page):
            words = words_by_page[pn - 1]
        # a moved box invalidates the old label/value; a re-save of the same box keeps them
        keep_prev = prev if (prev and raw.get("bbox") == prev.get("bbox")) else None
        built = build_field_anchor(raw, page_w, page_h, words, keep_prev)
        built["updated_at"] = _now()
        built["source"] = source
        new_fields[name] = built
        (updated if prev else added).append(name)

    la = {
        **{k: v for k, v in existing.items() if k not in ("fields", "history")},
        "fields": new_fields,
        "page_width": int(existing.get("page_width") or page_w or 1654),
        "page_height": int(existing.get("page_height") or page_h or 2338),
        "version": cur_v + 1,
        "schema": SCHEMA_VERSION,
        "updated_at": _now(),
    }
    if verified is not None:
        la["is_verified"] = bool(verified)
        if verified:
            la["verified_at"] = _now()

    history = list(existing.get("history") or [])
    if old_fields:
        history.append({"version": cur_v, "saved_at": _now(), "fields": old_fields,
                        "is_verified": bool(existing.get("is_verified"))})
    la["history"] = history[-HISTORY_LIMIT:]
    return la, {"added": added, "updated": updated, "kept": [n for n in old_fields if n not in (incoming or {})],
                "version": la["version"]}


def remove_anchor_field(existing: dict | None, name: str) -> dict:
    la = copy.deepcopy(existing or {})
    fields = dict(la.get("fields") or {})
    if name in fields:
        history = list(la.get("history") or [])
        history.append({"version": int(la.get("version") or 0), "saved_at": _now(),
                        "fields": dict(fields), "is_verified": bool(la.get("is_verified"))})
        la["history"] = history[-HISTORY_LIMIT:]
        del fields[name]
        la["version"] = int(la.get("version") or 0) + 1
        la["updated_at"] = _now()
    la["fields"] = fields
    return la


def rollback(existing: dict | None, to_version: int | None = None) -> dict:
    """Restore the fields of a previous version (default: the latest history entry)."""
    la = copy.deepcopy(existing or {})
    history = list(la.get("history") or [])
    if not history:
        raise ValueError("no earlier anchor version to roll back to")
    if to_version is None:
        snap = history[-1]
    else:
        match = [h for h in history if int(h.get("version", -1)) == int(to_version)]
        if not match:
            raise ValueError(f"anchor version {to_version} is not in history")
        snap = match[-1]
    history.append({"version": int(la.get("version") or 0), "saved_at": _now(),
                    "fields": dict(la.get("fields") or {}), "is_verified": bool(la.get("is_verified"))})
    la["fields"] = dict(snap["fields"])
    la["is_verified"] = bool(snap.get("is_verified"))
    la["version"] = int(la.get("version") or 0) + 1
    la["updated_at"] = _now()
    la["history"] = history[-HISTORY_LIMIT:]
    return la
