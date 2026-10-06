"""
Phase 13 — the multi-pipeline field consensus engine (app/ai/consensus.py),
backend core of the Orchestration page. Pure-logic tests, no DB/pipelines.
"""
from app.ai.consensus import (
    PipelineFieldValue,
    compare_field_across_pipelines,
    compare_pipeline_results,
    overall_consensus_rate,
)


def _v(pipeline, value, confidence):
    return PipelineFieldValue(pipeline_name=pipeline, value=value, confidence=confidence)


def test_unanimous_agreement():
    values = [_v("surya", "INV-001", 0.9), _v("paddle", "INV-001", 0.93), _v("qwen", "INV-001", 0.95)]
    result = compare_field_across_pipelines("invoice_number", values)
    assert result.resolution_rule == "unanimous"
    assert result.agreement is True
    assert result.needs_review is False
    assert result.consensus_value == "INV-001"


def test_agreement_is_case_and_whitespace_insensitive():
    values = [_v("surya", "INV-001", 0.9), _v("paddle", " inv-001 ", 0.8), _v("qwen", "INV-001", 0.9)]
    result = compare_field_across_pipelines("invoice_number", values)
    assert result.resolution_rule == "unanimous"
    assert result.needs_review is False


def test_two_of_three_majority():
    values = [_v("surya", "SD-881290-X", 0.82), _v("paddle", "SD-881290-K", 0.91), _v("qwen", "SD-881290-X", 0.94)]
    result = compare_field_across_pipelines("stamp_duty_number", values)
    assert result.resolution_rule == "majority"
    assert result.agreement is False
    assert result.needs_review is False
    assert result.consensus_value == "SD-881290-X"


def test_no_majority_falls_back_to_highest_confidence_and_flags_for_review():
    values = [_v("surya", "A", 0.80), _v("paddle", "B", 0.95), _v("qwen", "C", 0.70)]
    result = compare_field_across_pipelines("ambiguous_field", values)
    assert result.resolution_rule == "highest_confidence"
    assert result.needs_review is True
    assert result.consensus_value == "B"  # paddle had the highest confidence


def test_single_pipeline_reporting_a_field_is_not_a_discrepancy():
    values = [_v("qwen", "only value", 0.6)]
    result = compare_field_across_pipelines("rare_field", values)
    assert result.resolution_rule == "single_pipeline"
    assert result.needs_review is False
    assert result.consensus_value == "only value"


def test_field_with_no_values_at_all_needs_review():
    result = compare_field_across_pipelines("missing_everywhere", [])
    assert result.resolution_rule == "no_data"
    assert result.needs_review is True
    assert result.consensus_value is None


def test_compare_pipeline_results_never_counts_a_missing_field_as_a_disagreeing_vote():
    # "qwen" simply didn't report `notes` — that must not count as qwen
    # voting for an empty string, which would wrongly create a discrepancy.
    results_by_pipeline = {
        "surya": {"invoice_number": ("INV-1", 0.9), "notes": ("handwritten note", 0.6)},
        "qwen": {"invoice_number": ("INV-1", 0.95)},
    }
    comparisons = compare_pipeline_results(results_by_pipeline)
    by_name = {c.field_name: c for c in comparisons}

    assert by_name["invoice_number"].resolution_rule == "unanimous"
    assert by_name["notes"].resolution_rule == "single_pipeline"
    assert by_name["notes"].needs_review is False


def test_overall_consensus_rate():
    results_by_pipeline = {
        "surya": {"a": ("1", 0.9), "b": ("x", 0.8)},
        "qwen": {"a": ("1", 0.9), "b": ("y", 0.9)},
    }
    comparisons = compare_pipeline_results(results_by_pipeline)
    # "a" unanimous (no review), "b" a 1-1 tie with no majority (needs review)
    assert overall_consensus_rate(comparisons) == 0.5


def test_overall_consensus_rate_of_empty_list_is_zero():
    assert overall_consensus_rate([]) == 0.0
