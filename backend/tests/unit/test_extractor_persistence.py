"""
Phase 2 / Phase 25 — unit tests for the pure field-extraction-row builder
introduced in app/services/extractor.py.

No database involved: ExtractionResult/FieldConfidence are plain Pydantic
models, so this exercises the real row-shaping logic without needing Oracle.
"""
from app.ai.base import ExtractionResult, FieldConfidence
from app.services.extractor import build_field_extraction_rows


def _make_result(parsed_data, confidences):
    return ExtractionResult(
        raw_json="{}",
        parsed_data=parsed_data,
        confidence_scores=[
            FieldConfidence(field_name=name, confidence_score=score)
            for name, score in confidences.items()
        ],
        engine_name="qwen_vlm",
        model_name="qwen2.5vl:3b",
    )


def test_builds_one_row_per_field_with_matching_confidence():
    ai_result = _make_result(
        parsed_data={"invoice_number": "INV-001", "total": 42.5},
        confidences={"invoice_number": 0.97, "total": 0.88},
    )

    rows = build_field_extraction_rows("job-1", "run-1", ai_result)

    assert len(rows) == 2
    by_name = {r["field_name"]: r for r in rows}
    assert by_name["invoice_number"]["field_value"] == "INV-001"
    assert by_name["invoice_number"]["confidence"] == 0.97
    assert by_name["total"]["field_value"] == "42.5"
    assert by_name["total"]["confidence"] == 0.88
    for row in rows:
        assert row["job_id"] == "job-1"
        assert row["pipeline_run_id"] == "run-1"
        assert row["extraction_method"] == "qwen_vlm"
        assert row["is_final"] == 1


def test_field_with_no_confidence_score_gets_none_not_a_crash():
    # A field present in parsed_data but missing from confidence_scores (e.g. the
    # LLM returned a key the confidence step didn't score) must not raise — it
    # should just persist with confidence=None rather than failing the whole job.
    ai_result = _make_result(
        parsed_data={"invoice_number": "INV-002", "notes": "handwritten margin note"},
        confidences={"invoice_number": 0.91},
    )

    rows = build_field_extraction_rows("job-2", "run-2", ai_result)

    by_name = {r["field_name"]: r for r in rows}
    assert by_name["notes"]["confidence"] is None
    assert by_name["notes"]["field_value"] == "handwritten margin note"


def test_empty_parsed_data_yields_no_rows():
    ai_result = _make_result(parsed_data={}, confidences={})
    assert build_field_extraction_rows("job-3", "run-3", ai_result) == []


def test_none_field_value_is_preserved_as_none_not_stringified():
    ai_result = _make_result(
        parsed_data={"optional_field": None},
        confidences={"optional_field": 0.5},
    )
    rows = build_field_extraction_rows("job-4", "run-4", ai_result)
    assert rows[0]["field_value"] is None


def test_low_confidence_field_is_flagged_needs_review_phase9():
    ai_result = _make_result(
        parsed_data={"invoice_number": "INV-005", "total": 99.0},
        confidences={"invoice_number": 0.95, "total": 0.30},
    )

    rows = build_field_extraction_rows("job-5", "run-5", ai_result, confidence_threshold=0.75)

    by_name = {r["field_name"]: r for r in rows}
    assert by_name["invoice_number"]["fallback_method"] is None
    assert by_name["total"]["fallback_method"] == "needs_review"


def test_field_missing_confidence_is_also_flagged_needs_review():
    ai_result = _make_result(
        parsed_data={"invoice_number": "INV-006", "notes": "unscored field"},
        confidences={"invoice_number": 0.95},
    )

    rows = build_field_extraction_rows("job-6", "run-6", ai_result, confidence_threshold=0.75)

    by_name = {r["field_name"]: r for r in rows}
    assert by_name["notes"]["fallback_method"] == "needs_review"
    assert by_name["invoice_number"]["fallback_method"] is None


def test_default_threshold_comes_from_settings_when_not_passed():
    # No explicit confidence_threshold kwarg -> falls back to
    # settings.CONFIDENCE_THRESHOLD (0.75 by default, see app/core/config.py).
    ai_result = _make_result(
        parsed_data={"total": 50.0},
        confidences={"total": 0.50},
    )
    rows = build_field_extraction_rows("job-7", "run-7", ai_result)
    assert rows[0]["fallback_method"] == "needs_review"
