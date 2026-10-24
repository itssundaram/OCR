"""
Adding a field that was found on a document to the template ("Found on document" -> template field).

Why this is an explicit, additive, logged change and not automatic
  * It is permanent for every later document of the template, so it only happens when a person asks for it.
  * Adding a field is backward compatible (earlier results keep their data); removing or redefining one is not,
    so those stay in the template editor / a new template version.
  * The field is defined by its PRINTED LABEL (an alias), not by a position: it keeps working when the layout
    shifts, and it cannot revive the stale-anchor problem.
  * Every change is written to template_json["change_log"] (who/what/when/which job), so it can be audited and
    undone in the template editor.
"""
from __future__ import annotations

import datetime
import re
from typing import Any

from app.core.exceptions import DocIntError

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
MAX_LOG = 200


def slug_name(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    if s and not s[0].isalpha():
        s = "f_" + s
    return s[:60].strip("_")


def infer_type(value: str) -> str:
    v = str(value or "").strip()
    if re.fullmatch(r"\d{1,2}[-./ ]+(\d{1,2}|[A-Za-z]{3,9})[-./ ]+\d{2,4}", v):
        return "date"
    if re.fullmatch(r"[-+]?\d+([.,]\d+)?", v):
        return "number"
    return "text"


def _alias_slugs(f: dict) -> set[str]:
    return {slug_name(a) for a in (f.get("aliases") or [])} | {slug_name(f.get(k)) for k in ("name", "label", "display_name") if f.get(k)}


def add_field(template_json: dict, *, name: str, label: str, value: str, field_type: str | None,
              job_id: str, who: str = "review") -> tuple[dict, dict]:
    """Returns (new template_json, the field definition added). Raises DocIntError on a bad or clashing name."""
    name = slug_name(name)
    if not NAME_RE.match(name):
        raise DocIntError("Field name must start with a letter and use only letters, digits and underscores.", "INVALID_FIELD_NAME")
    tj: dict[str, Any] = dict(template_json or {})
    fields = [dict(f) for f in (tj.get("fields") or []) if isinstance(f, dict)]
    for f in fields:
        if slug_name(f.get("name")) == name:
            raise DocIntError(f"The template already has a field named '{name}'.", "FIELD_ALREADY_IN_TEMPLATE")
    label = " ".join(str(label or "").split())[:80]
    # a printed label some other field already answers to would make two fields compete for the same value
    if label and f"{slug_name(label)}" in {s for f in fields for s in _alias_slugs(f) if slug_name(f.get("name")) != name}:
        raise DocIntError(f"Another template field already uses the label '{label}'.", "LABEL_ALREADY_IN_TEMPLATE")
    ftype = field_type if field_type in ("text", "date", "number", "boolean") else infer_type(value)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    fdef = {
        "name": name, "type": ftype,
        "description": f"Printed on the document as \"{label}\"." if label else "Added from a document.",
        "example": str(value or "")[:120] or None, "required": False,
        "aliases": [label] if label and slug_name(label) != name else [],
        "extraction_hint": None, "known_values": [],
        "origin": {"added_from": "found_on_document", "job_id": job_id, "label": label, "at": now},
    }
    fields.append(fdef)
    tj["fields"] = fields
    log = list(tj.get("change_log") or [])
    log.append({"at": now, "action": "add_field", "field": name, "label": label, "job_id": job_id, "by": who})
    tj["change_log"] = log[-MAX_LOG:]
    return tj, fdef
