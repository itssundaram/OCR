"""
Phase 4 / Phase 25 — tests for the pipeline plugin interface and registry.

Pure logic only: dummy pipeline subclasses exercise OCRPipeline.run()'s default
behavior (success, a failed extract_fields, a failed extract_tables) without
touching Oracle, Ollama, or any real OCR engine. A separate test confirms the
two real adapters (QwenPipeline, LegacyOCRPipeline) actually register.
"""
from app.pipelines.base import FieldResult, OCRPipeline, PipelineCapabilities, PipelineResult, TableResult
from app.pipelines.registry import get_pipeline, list_pipelines, register_pipeline


class _HappyPipeline(OCRPipeline):
    @property
    def name(self) -> str:
        return "_happy_test_pipeline"

    @property
    def capabilities(self) -> PipelineCapabilities:
        return PipelineCapabilities()

    def preprocess(self, image, config):
        return image

    def detect_regions(self, image):
        return []

    def extract_tables(self, pages, job_id):
        return [TableResult(table_index=0, page_number=1)]

    def extract_fields(self, pages, template_fields, job_id, **kwargs):
        return PipelineResult(
            pipeline_name=self.name,
            status="completed",
            fields=[FieldResult(field_name="x", value="1", confidence=0.9, page_number=1)],
        )


class _FieldsFailPipeline(_HappyPipeline):
    @property
    def name(self) -> str:
        return "_fields_fail_test_pipeline"

    def extract_fields(self, pages, template_fields, job_id, **kwargs):
        raise RuntimeError("boom")


class _TablesFailPipeline(_HappyPipeline):
    @property
    def name(self) -> str:
        return "_tables_fail_test_pipeline"

    def extract_tables(self, pages, job_id):
        raise RuntimeError("table engine down")


def test_run_merges_fields_and_tables_on_success():
    events = []
    result = _HappyPipeline().run([], {}, "job-1", event_callback=lambda t, d: events.append(t))

    assert result.status == "completed"
    assert len(result.fields) == 1
    assert len(result.tables) == 1
    assert result.processing_time_ms >= 0
    assert events == ["PIPELINE_STARTED", "PIPELINE_COMPLETED"]


def test_run_turns_a_failed_extract_fields_into_a_failed_result_not_an_exception():
    events = []
    result = _FieldsFailPipeline().run([], {}, "job-2", event_callback=lambda t, d: events.append(t))

    assert result.status == "failed"
    assert "boom" in result.error_message
    assert events == ["PIPELINE_STARTED", "PIPELINE_FAILED"]


def test_run_marks_partial_when_only_extract_tables_fails_but_keeps_fields():
    result = _TablesFailPipeline().run([], {}, "job-3")

    assert result.status == "partial"
    assert len(result.fields) == 1  # fields from extract_fields are preserved
    assert "warnings" in result.metadata


def test_run_passes_native_texts_and_schema_through_from_the_template_dict():
    captured = {}

    class _CapturingPipeline(_HappyPipeline):
        def extract_fields(self, pages, template_fields, job_id, **kwargs):
            captured.update(kwargs)
            return super().extract_fields(pages, template_fields, job_id, **kwargs)

    _CapturingPipeline().run(
        [], {"schema": {"a": 1}, "native_texts": ["hello"], "department": "FINANCE"}, "job-4"
    )

    assert captured["schema"] == {"a": 1}
    assert captured["native_texts"] == ["hello"]
    assert captured["department"] == "FINANCE"


def test_builtin_pipelines_are_registered_on_import():
    import app.pipelines  # noqa: F401 — triggers @register_pipeline for all three adapters

    names = list_pipelines()
    assert "qwen" in names
    assert "legacy_ocr" in names
    assert "surya" in names
    assert get_pipeline("qwen").name == "qwen"
    assert get_pipeline("legacy_ocr").name == "legacy_ocr"
    assert get_pipeline("surya").name == "surya"


def test_surya_pipeline_is_selectable_independent_of_ocr_strategy_setting():
    # Phase 6's actual point: the standalone SuryaPipeline must be choosable
    # through the registry regardless of the global OCR_STRATEGY setting
    # (which still separately drives the legacy inline dispatch).
    import app.pipelines  # noqa: F401
    from app.core.config import settings

    original = settings.OCR_STRATEGY
    try:
        settings.OCR_STRATEGY = "tesseract_only"
        pipeline = get_pipeline("surya")
        assert pipeline.name == "surya"
        assert pipeline.capabilities.requires_gpu is True
    finally:
        settings.OCR_STRATEGY = original


def test_surya_pipeline_extract_tables_returns_empty_list_same_as_legacy_ocr():
    # Tables are folded into extract_fields' per-page OCRResult (via the
    # region processor's Table-region handling), not a separate pass —
    # same contract as LegacyOCRPipeline.
    import app.pipelines  # noqa: F401

    pipeline = get_pipeline("surya")
    assert pipeline.extract_tables([], "job-x") == []
