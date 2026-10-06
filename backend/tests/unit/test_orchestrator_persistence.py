"""
Phase 13 — unit tests for the pure row-builder in app/services/orchestrator.py
that turns a PipelineResult (Phase 4 interface) into FIELD_EXTRACTIONS row
dicts. Sibling of tests/unit/test_extractor_persistence.py, which does the
same job for the legacy ExtractionResult shape.

No database involved: FieldResult/PipelineResult are plain dataclasses.
"""
from app.pipelines.base import FieldResult, PipelineResult
from app.services.orchestrator import build_field_extraction_rows_from_pipeline_result


def _result(fields: dict[str, tuple[str, float]]) -> PipelineResult:
    return PipelineResult(
        pipeline_name="surya",
        status="completed",
        fields=[
            FieldResult(field_name=name, value=value, confidence=conf, page_number=1, extraction_method="surya")
            for name, (value, conf) in fields.items()
        ],
    )


def test_builds_one_row_per_field_with_matching_confidence():
    result = _result({"invoice_number": ("INV-001", 0.97), "total": ("42.5", 0.88)})

    rows = build_field_extraction_rows_from_pipeline_result("job-1", "run-1", result, confidence_threshold=0.75)

    assert len(rows) == 2
    by_name = {r["field_name"]: r for r in rows}
    assert by_name["invoice_number"]["field_value"] == "INV-001"
    assert by_name["invoice_number"]["confidence"] == 0.97
    assert by_name["invoice_number"]["fallback_method"] is None
    for row in rows:
        assert row["job_id"] == "job-1"
        assert row["pipeline_run_id"] == "run-1"
        assert row["extraction_method"] == "surya"


def test_low_confidence_field_is_flagged_needs_review():
    result = _result({"invoice_number": ("INV-002", 0.95), "total": ("1.0", 0.30)})

    rows = build_field_extraction_rows_from_pipeline_result("job-2", "run-2", result, confidence_threshold=0.75)

    by_name = {r["field_name"]: r for r in rows}
    assert by_name["invoice_number"]["fallback_method"] is None
    assert by_name["total"]["fallback_method"] == "needs_review"


def test_is_final_flag_is_passed_through():
    result = _result({"a": ("1", 0.9)})
    final_rows = build_field_extraction_rows_from_pipeline_result("job-3", "run-3", result, is_final=1)
    raw_rows = build_field_extraction_rows_from_pipeline_result("job-3", "run-3", result, is_final=0)
    assert final_rows[0]["is_final"] == 1
    assert raw_rows[0]["is_final"] == 0


def test_empty_fields_yields_no_rows():
    result = _result({})
    assert build_field_extraction_rows_from_pipeline_result("job-4", "run-4", result) == []


def test_default_threshold_comes_from_settings_when_not_passed():
    result = _result({"total": ("50.0", 0.50)})
    rows = build_field_extraction_rows_from_pipeline_result("job-5", "run-5", result)
    assert rows[0]["fallback_method"] == "needs_review"


def test_page_id_resolved_from_page_ids_map():
    """Phase 16 groundwork: a field's page_number must resolve to the right
    DOCUMENT_PAGES row id via the page_ids map passed in from _load_job_context,
    so the evidence endpoint can later join FIELD_EXTRACTIONS -> DOCUMENT_PAGES."""
    result = _result({"a": ("1", 0.9)})
    rows = build_field_extraction_rows_from_pipeline_result(
        "job-6", "run-6", result, page_ids={1: "page-uuid-1", 2: "page-uuid-2"},
    )
    assert rows[0]["page_id"] == "page-uuid-1"


def test_page_id_is_none_when_page_ids_not_supplied():
    result = _result({"a": ("1", 0.9)})
    rows = build_field_extraction_rows_from_pipeline_result("job-7", "run-7", result)
    assert rows[0]["page_id"] is None
