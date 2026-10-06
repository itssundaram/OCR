"""
DOCINT — Field-level confidence classification & fallback selection
Phase 9 of the implementation plan.

Master prompt section 9's rule: "fallback must happen at the field/region
level, not automatically at the entire document level." This module is the
decision layer that enforces that — it never names a whole document as
needing retry, only the specific fields that fell below
settings.CONFIDENCE_THRESHOLD.

Pure functions only: no DB, no OCR/LLM calls, nothing to mock. That makes
this testable without Oracle or a GPU, consistent with this project's "no
mocks, real Oracle DB" test convention (tests/conftest.py) — there's simply
nothing here that convention would ask us to mock.

Scope note (read before extending): this module only classifies and selects.
Actually *executing* a field-level retry (re-OCR a crop at higher
resolution, or a crop-only VLM call) needs two things this codebase doesn't
have yet: per-field bbox/crop plumbing (Phase 16 — the evidence API) and a
second pipeline already producing an independent field value to retry
against (Phase 6 — Surya pulled out as its own pipeline). Wiring a fake or
untested retry call into the hot path before those exist would be the kind
of speculative plumbing the plan's own Phase 15 note warns against. So
`FIELD_LEVEL_FALLBACK_ENABLED` in config.py currently only gates whether
low-confidence fields get flagged and logged — not re-extracted.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FieldReviewStatus:
    field_name: str
    confidence: float | None
    needs_review: bool
    reason: str


def classify_field_confidence(
    field_name: str,
    confidence: float | None,
    threshold: float,
) -> FieldReviewStatus:
    """One field's pass/fail call against the configured threshold.

    A missing confidence score (None) is treated as needing review rather
    than silently passing — better to over-flag than to hide a field nobody
    scored at all.
    """
    if confidence is None:
        return FieldReviewStatus(
            field_name=field_name,
            confidence=None,
            needs_review=True,
            reason="no confidence score computed",
        )
    if confidence < threshold:
        return FieldReviewStatus(
            field_name=field_name,
            confidence=confidence,
            needs_review=True,
            reason=f"confidence {confidence:.2f} below threshold {threshold:.2f}",
        )
    return FieldReviewStatus(
        field_name=field_name,
        confidence=confidence,
        needs_review=False,
        reason="above threshold",
    )


def classify_all_fields(
    conf_by_field: dict[str, float | None],
    threshold: float,
) -> list[FieldReviewStatus]:
    """Classify every field independently — the field-level unit the master
    prompt asks for, as opposed to one confidence number for the document."""
    return [
        classify_field_confidence(name, conf, threshold)
        for name, conf in conf_by_field.items()
    ]


def fields_needing_fallback(statuses: list[FieldReviewStatus]) -> list[str]:
    """The retry list: only these field names, never "the whole document"."""
    return [s.field_name for s in statuses if s.needs_review]


def document_fast_path_eligible(
    statuses: list[FieldReviewStatus],
    *,
    validation_passed: bool,
) -> bool:
    """True when every field already cleared the threshold AND schema
    validation passed — the case where a second (LLM) pass would add nothing.

    This is the decision point for master prompt section 9/10's "skip LLM
    calls entirely" fast path. Nothing in the hot path calls this yet: there
    is no pre-LLM source of field values in this codebase today (the
    `FieldMatcher` referenced in app/ai/response_builder.py's docstring was
    never built), so there is nothing to fast-path around until one exists.
    Kept here, tested, and ready for that pipeline rather than stubbed with
    fake inputs.
    """
    if not statuses:
        return False
    return validation_passed and not any(s.needs_review for s in statuses)
