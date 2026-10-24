"""run_pipeline_comparison_job end to end (in-memory SQLite, stub pipelines, a real one-page PDF).

Nothing here needs a model: the pipelines are stubs registered under test names. What is exercised is the
orchestrator itself — the persistence of every pipeline's rows, the consensus run, the result envelope shared
with single-pipeline jobs, the handling of a pipeline that raises or reports failure, and the read-only flag
that keeps a comparison from rewriting the template.
"""
import uuid

import fitz
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db.models  # noqa: F401
import app.services.extractor as extractor
import app.services.orchestrator as orch
from app.core.config import settings
from app.db.database import Base
from app.db.models import (
    Department, Document, FieldExtraction, PipelineRun, ProcessingEvent, ProcessingJob, ProcessingResult, Template,
)
from app.pipelines.base import FieldResult, OCRPipeline, PipelineCapabilities, PipelineResult, TableResult
from app.pipelines.registry import _REGISTRY, register_pipeline
from app.services.export_json import build_export

SEEN_TEMPLATE_FLAGS: dict[str, dict] = {}


def _fr(name, value, conf=0.9, source=""):
    return FieldResult(field_name=name, value=value, confidence=conf, page_number=1, extraction_method="stub",
                       label=name.upper() if source else "", source=source)


def _table():
    return TableResult(table_index=1, page_number=1, headers=["Item", "Qty"], rows=[["Bolt", "4"], ["Nut", "9"]],
                       confidence=0.8, extraction_method="stub")


class _Stub(OCRPipeline):
    NAME = ""
    FIELDS: list = []
    TABLES: list = []
    RAISE: str | None = None
    FAIL: str | None = None

    @property
    def name(self):
        return self.NAME

    @property
    def capabilities(self):
        return PipelineCapabilities()

    def extract_tables(self, pages, job_id):
        return list(self.TABLES)

    def extract_fields(self, pages, template_fields, job_id, **kwargs):
        SEEN_TEMPLATE_FLAGS[self.NAME] = {"read_only": kwargs.get("read_only")}
        if self.RAISE:
            raise RuntimeError(self.RAISE)
        if self.FAIL:
            return PipelineResult(pipeline_name=self.NAME, status="failed", error_message=self.FAIL)
        return PipelineResult(pipeline_name=self.NAME, status="completed", fields=list(self.FIELDS),
                              overall_confidence=0.9)


def _register(name, **attrs):
    cls = type(f"Stub_{name}", (_Stub,), {"NAME": name, **attrs})
    register_pipeline(name)(cls)
    return cls


@pytest.fixture(autouse=True)
def _cleanup_registry():
    before = set(_REGISTRY)
    SEEN_TEMPLATE_FLAGS.clear()
    yield
    for k in set(_REGISTRY) - before:
        _REGISTRY.pop(k, None)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    # a file database, not StaticPool: the pipelines' event callbacks write from worker threads,
    # and one shared in-memory connection cannot commit from two threads at once
    eng = create_engine(f"sqlite:///{tmp_path / 'orch.db'}", connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(orch, "SessionLocal", S)
    monkeypatch.setattr(extractor, "SessionLocal", S)
    monkeypatch.setattr(type(settings), "storage_path", property(lambda self: tmp_path), raising=False)

    pdf = tmp_path / "doc.pdf"
    d = fitz.open()
    d.new_page().insert_text((72, 100), "Issue Voucher No: 123 \nUnit From: 5 Corps", fontsize=12)
    d.save(str(pdf))
    d.close()

    with S() as db:
        dep = Department(slug="TMS", name="TMS"); db.add(dep); db.flush()
        tmpl = Template(department_id=dep.id, code="backload", version=1, is_active=1, template_json={"fields": [
            {"name": "iv_no", "type": "text", "description": "d"},
            {"name": "unit_from", "type": "text", "description": "d"},
            {"name": "items", "type": "table", "description": "d"},
        ]})
        db.add(tmpl); db.flush()
        doc = Document(id=str(uuid.uuid4()), original_filename="doc.pdf", stored_filename="x", file_path=str(pdf),
                       file_type="application/pdf", file_size_bytes=1, template_id=tmpl.id, status="QUEUED")
        db.add(doc); db.flush()
        job = ProcessingJob(id=str(uuid.uuid4()), document_id=doc.id, template_id=tmpl.id, template_version=1,
                            status="QUEUED", pipeline_mode="orchestration")
        db.add(job); db.commit()
        job_id = job.id
    return S, job_id


def _state(S, job_id):
    with S() as db:
        job = db.get(ProcessingJob, job_id)
        runs = {r.pipeline_name: r for r in db.query(PipelineRun).filter_by(job_id=job_id)}
        res = db.query(ProcessingResult).filter_by(job_id=job_id).first()
        events = [e.event_type for e in db.query(ProcessingEvent).filter_by(job_id=job_id)]
        final = {f.field_name: f for f in db.query(FieldExtraction).filter_by(job_id=job_id, is_final=1)}
        return job, runs, res, events, final


def test_two_pipelines_agree_and_disagree_with_a_shared_envelope(env):
    S, job_id = env
    _register("_stub_a", FIELDS=[_fr("iv_no", "123"), _fr("unit_from", "5 CORPS", 0.95), _fr("remarks", "ok", 0.7, "discovered")],
              TABLES=[_table()])
    _register("_stub_b", FIELDS=[_fr("iv_no", "123"), _fr("unit_from", "9 ASSAM", 0.6)])
    orch.run_pipeline_comparison_job(job_id, ["_stub_a", "_stub_b"])
    job, runs, res, events, final = _state(S, job_id)

    # the crash this guards: "_page_number" etc. must never reach FieldExtraction(**row)
    assert job.status == "WARNING", job.error_message          # unit_from has no majority -> review
    assert {r.status for n, r in runs.items() if n.startswith("_stub")} == {"COMPLETED"}
    assert runs["consensus"].status == "COMPLETED"

    ej = res.extracted_json
    assert ej["extraction_method"] == "consensus" and ej["pages_processed"] == 1
    assert ej["fields"]["iv_no"]["value"] == "123" and ej["fields"]["iv_no"]["resolution_rule"] == "unanimous"
    assert ej["fields"]["unit_from"]["value"] == "5 CORPS" and ej["fields"]["unit_from"]["needs_review"] is True
    assert ej["fields"]["unit_from"]["page_number"] == 1
    # discovered fields stay out of the vote but are not lost
    assert ej["fields"]["remarks"]["source"] == "discovered"
    meta = res.ocr_metadata_json
    assert meta["total_fields"] == 2 and meta["flagged_fields"] == ["unit_from"]
    assert meta["consensus_rate"] == 0.5 and meta["cross_checked"] is True and meta["degraded"] is False
    # tables come from one pipeline (the one that read any) and the export works on the result
    assert ej["table_source_pipeline"] == "_stub_a" and ej["tables"][0]["rows"] == [["Bolt", "4"], ["Nut", "9"]]
    ex = build_export(ej)
    assert ex["fields"]["iv_no"] == "123" and ex["additional_fields"] == {"remarks": "ok"} and len(ex["tables"]) == 1
    assert set(final) == {"iv_no", "unit_from", "remarks"}
    assert "PIPELINE_DEGRADED" not in events


def test_comparison_is_read_only_for_the_template(env):
    S, job_id = env
    _register("_stub_a", FIELDS=[_fr("iv_no", "1")])
    _register("_stub_b", FIELDS=[_fr("iv_no", "1")])
    orch.run_pipeline_comparison_job(job_id, ["_stub_a", "_stub_b"])
    assert SEEN_TEMPLATE_FLAGS == {"_stub_a": {"read_only": True}, "_stub_b": {"read_only": True}}


def test_a_pipeline_that_raises_is_closed_as_failed_and_the_job_is_degraded(env):
    S, job_id = env
    _register("_stub_a", FIELDS=[_fr("iv_no", "123"), _fr("unit_from", "5 CORPS")], TABLES=[_table()])
    _register("_stub_b", RAISE="No module named 'surya'")
    orch.run_pipeline_comparison_job(job_id, ["_stub_a", "_stub_b"])
    job, runs, res, events, final = _state(S, job_id)

    assert job.status == "WARNING"                                  # never a silent COMPLETED
    assert runs["_stub_b"].status == "FAILED" and "surya" in runs["_stub_b"].error_message
    assert runs["_stub_a"].status == "COMPLETED"
    assert "PIPELINE_FAILED" in events and "PIPELINE_DEGRADED" in events
    meta = res.ocr_metadata_json
    assert meta["degraded"] is True and meta["cross_checked"] is False and meta["failed_pipelines"] == ["_stub_b"]
    assert "surya" in meta["pipeline_errors"]["_stub_b"]
    # nothing was cross-checked, so nothing may read as consensus
    assert all(f["needs_review"] for n, f in res.extracted_json["fields"].items())
    assert meta["consensus_rate"] == 0.0


def test_a_pipeline_reporting_failure_keeps_its_reason(env):
    S, job_id = env
    _register("_stub_a", FIELDS=[_fr("iv_no", "1")])
    _register("_stub_b", FAIL="vision model offline")
    orch.run_pipeline_comparison_job(job_id, ["_stub_a", "_stub_b"])
    job, runs, res, events, final = _state(S, job_id)
    assert job.status == "WARNING"
    assert runs["_stub_b"].status == "FAILED" and runs["_stub_b"].error_message == "vision model offline"
    assert res.ocr_metadata_json["pipeline_errors"] == {"_stub_b": "vision model offline"}


def test_when_every_pipeline_fails_the_job_fails_and_no_run_is_left_running(env):
    S, job_id = env
    _register("_stub_a", RAISE="boom a")
    _register("_stub_b", FAIL="boom b")
    orch.run_pipeline_comparison_job(job_id, ["_stub_a", "_stub_b"])
    job, runs, res, events, final = _state(S, job_id)
    assert job.status == "FAILED"
    assert "boom a" in job.error_message and "boom b" in job.error_message
    assert {r.status for r in runs.values()} == {"FAILED"}
    assert res is None


def test_tables_from_a_pipeline_without_table_fields_are_not_dropped_when_it_reads_them_anyway(env):
    from app.pipelines.base import PipelineCapabilities as Caps

    class _Tabular(_Stub):
        NAME = "_stub_tabular"
        FIELDS = [_fr("iv_no", "1")]
        TABLES = [_table()]

        @property
        def capabilities(self):
            return Caps(tables_with_fields=True)

    register_pipeline("_stub_tabular")(_Tabular)
    r = _Tabular().run([], {"fields": [{"name": "iv_no", "type": "text"}]}, "job-x")
    assert len(r.tables) == 1                                       # reads tables with no table field declared

    class _Plain(_Tabular):
        NAME = "_stub_plain"

        @property
        def capabilities(self):
            return Caps()

    r = _Plain().run([], {"fields": [{"name": "iv_no", "type": "text"}]}, "job-y")
    assert r.tables == []                                           # a costly separate table pass is still skipped
