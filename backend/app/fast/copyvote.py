"""Cross-copy choice: a voucher is often scanned together with a photo or carbon of its copy (BACKLOAD2: page 1 = the
original, page 2 = a phone photo of the copy). The field is read on every copy; the reading that is kept as THE
value is the best one, not just the first page's.

Rules (deliberately conservative - a copy never replaces a reading that is already good):
  1. a reading that passes the field's checks (date parses, pattern matches, has the digits a number needs) beats
     one that fails them, whatever the confidence;
  2. among readings that are equally valid, a clearly more confident one wins (CONF_MARGIN);
  3. otherwise the first copy (the primary) stays.
A reading only competes when it is a re-reading of the SAME text (SAME_TEXT similarity to the primary): a different
voucher in the same PDF has its own values and never replaces this one.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Callable

from app.fast.validate import assess

CONF_MARGIN = 0.15
SAME_TEXT = 0.6


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(t or "").lower())


def same_text(a: str, b: str) -> bool:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio() >= SAME_TEXT


def _valid(fdef: dict, value: str, confidence: float, assess_fn: Callable = assess) -> bool:
    a = assess_fn(fdef, value, 1.0)           # confidence 1.0: only the content checks decide validity here
    return a.status == "validated"


def pick_best_copy(fdef: dict, primary: dict, others: list[dict], assess_fn: Callable = assess) -> int:
    """Index in [primary] + others of the reading to keep (0 = the primary)."""
    cands = [primary] + list(others)
    ok = [_valid(fdef, str(c.get("value") or ""), float(c.get("confidence") or 0), assess_fn) for c in cands]
    best = 0
    for i in range(1, len(cands)):
        if not same_text(cands[0].get("value"), cands[i].get("value")):
            continue
        if ok[i] and not ok[best]:
            best = i
        elif ok[i] == ok[best] and float(cands[i].get("confidence") or 0) >= float(cands[best].get("confidence") or 0) + CONF_MARGIN:
            best = i
    return best
