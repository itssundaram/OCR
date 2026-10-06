"""
Phase 9 — field-level confidence classification & fallback selection.

Pure-logic tests against app/ai/field_fallback.py: no DB, no OCR/LLM calls.
"""
from app.ai.field_fallback import (
    classify_field_confidence,
    classify_all_fields,
    fields_needing_fallback,
    document_fast_path_eligible,
)


def test_field_above_threshold_does_not_need_review():
    status = classify_field_confidence("invoice_number", 0.92, threshold=0.75)
    assert status.needs_review is False
    assert status.confidence == 0.92


def test_field_below_threshold_needs_review():
    status = classify_field_confidence("notes", 0.40, threshold=0.75)
    assert status.needs_review is True
    assert "below threshold" in status.reason


def test_field_with_no_confidence_score_needs_review():
    # Missing confidence is treated as needing review rather than silently
    # passing — we'd rather over-flag than hide an unscored field.
    status = classify_field_confidence("mystery_field", None, threshold=0.75)
    assert status.needs_review is True
    assert status.confidence is None


def test_classify_all_fields_is_scoped_to_individual_fields():
    statuses = classify_all_fields(
        {"invoice_number": 0.95, "total": 0.40, "notes": None},
        threshold=0.75,
    )
    assert len(statuses) == 3
    by_name = {s.field_name: s for s in statuses}
    assert by_name["invoice_number"].needs_review is False
    assert by_name["total"].needs_review is True
    assert by_name["notes"].needs_review is True


def test_fields_needing_fallback_never_names_the_whole_document():
    statuses = classify_all_fields(
        {"invoice_number": 0.95, "total": 0.40},
        threshold=0.75,
    )
    flagged = fields_needing_fallback(statuses)
    assert flagged == ["total"]


def test_document_fast_path_eligible_requires_all_fields_clean_and_valid():
    all_good = classify_all_fields({"a": 0.9, "b": 0.8}, threshold=0.75)
    assert document_fast_path_eligible(all_good, validation_passed=True) is True
    assert document_fast_path_eligible(all_good, validation_passed=False) is False

    one_bad = classify_all_fields({"a": 0.9, "b": 0.3}, threshold=0.75)
    assert document_fast_path_eligible(one_bad, validation_passed=True) is False


def test_document_fast_path_eligible_false_for_empty_statuses():
    assert document_fast_path_eligible([], validation_passed=True) is False
