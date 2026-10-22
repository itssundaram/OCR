"""
Field validation. A value is `validated` only when its checks pass AND the
reader's confidence is high enough; OCR confidence alone never makes a value
"correct". Checks come from the template field (type, pattern, name).

States: validated | review_required.  (extracted = read but not checked yet,
failed = nothing found; those are set by the extractor.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.core.config import settings

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_NUM_DATE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$")
_ISO_DATE = re.compile(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$")
_DMY_TXT = re.compile(r"^(\d{1,2})[ \-/.]*([A-Za-z]{3,9})[ \-/.,]*(\d{2,4})$")
_MDY_TXT = re.compile(r"^([A-Za-z]{3,9})[ \-/.]*(\d{1,2})[ ,\-/.]+(\d{2,4})$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_PHONE = re.compile(r"^\+?[\d][\d\s\-()]{6,17}\d$")
_NUMBER = re.compile(r"^[-+(]?[^\d\s-]{0,3}\s?\d[\d,]*(\.\d+)?\)?$")


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if len(y) == 2 else n


def parse_date(text: str) -> date | None:
    t = (text or "").strip().strip(".,;:")
    try:
        m = _NUM_DATE.match(t)
        if m:
            return date(_year(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = _ISO_DATE.match(t)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _DMY_TXT.match(t)
        if m and m.group(2)[:3].lower() in _MONTHS:
            return date(_year(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        m = _MDY_TXT.match(t)
        if m and m.group(1)[:3].lower() in _MONTHS:
            return date(_year(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
    except ValueError:
        return None
    return None


@dataclass
class Assessment:
    status: str                       # validated | review_required
    checks: list[dict] = field(default_factory=list)
    reason: str = ""


def _kind(fdef: dict | None) -> str:
    if not fdef:
        return "text"
    t = str(fdef.get("type") or "").lower()
    name = " ".join(str(fdef.get(k) or "") for k in ("name", "label", "display_name")).lower()
    if t in ("date", "datetime") or re.search(r"\bdate\b|\bdated\b|_date\b|\bdt\b", name):
        return "date"
    if t in ("number", "integer", "int", "float", "decimal", "amount", "currency"):
        return "number"
    if t == "email" or "email" in name:
        return "email"
    if t in ("phone", "mobile") or "phone" in name or "mobile" in name:
        return "phone"
    return "text"


def assess(fdef: dict | None, value: str, confidence: float, *, supported: bool | None = None) -> Assessment:
    """`supported` is only passed for values from the VLM: True when the OCR text
    of the same crop backs the answer, False when it does not."""
    checks: list[dict] = []
    value = (value or "").strip()
    checks.append({"rule": "non_empty", "ok": bool(value)})
    kind = _kind(fdef)
    if value:
        if kind == "date":
            checks.append({"rule": "date", "ok": parse_date(value) is not None})
        elif kind == "number":
            checks.append({"rule": "number", "ok": bool(_NUMBER.match(value))})
        elif kind == "email":
            checks.append({"rule": "email", "ok": bool(_EMAIL.match(value))})
        elif kind == "phone":
            checks.append({"rule": "phone", "ok": bool(_PHONE.match(value))})
        pattern = (fdef or {}).get("pattern") or (fdef or {}).get("regex")
        if pattern:
            try:
                checks.append({"rule": "pattern", "ok": re.search(str(pattern), value) is not None})
            except re.error:
                pass
        mn, mx = (fdef or {}).get("min_length"), (fdef or {}).get("max_length")
        if isinstance(mn, int):
            checks.append({"rule": "min_length", "ok": len(value) >= mn})
        if isinstance(mx, int):
            checks.append({"rule": "max_length", "ok": len(value) <= mx})
    if supported is not None:
        checks.append({"rule": "ocr_supports_vlm", "ok": bool(supported)})
    failed = [c["rule"] for c in checks if not c["ok"]]
    if failed:
        return Assessment("review_required", checks, "failed: " + ", ".join(failed))
    if confidence < settings.CONFIDENCE_THRESHOLD:
        return Assessment("review_required", checks, f"low_confidence {confidence:.2f}")
    return Assessment("validated", checks, "")
