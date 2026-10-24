"""The whole prefetch -> commit -> status -> export -> edit flow over HTTP routes, on in-memory SQLite.
Routing (the new /extract/jobs/... paths next to the generic /{dept}/{template}/... ones), the form field, error
codes, list filters and the department JSON are exercised together; the queue is stubbed."""
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401
from app.core.config import settings
from app.db.database import Base, get_db
from app.db.models import Department, ProcessingJob, ProcessingResult, Template


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app.main import create_app
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    with S() as db:
        d = Department(slug="TMS", name="TMS"); db.add(d); db.flush()
        db.add(Template(department_id=d.id, code="backload", version=1, is_active=1,
                        template_json={"fields": [{"name": "iv_no", "type": "text", "description": "d"}]}))
        db.commit()
    monkeypatch.setattr(type(settings), "storage_path", property(lambda self: tmp_path), raising=False)
    import app.fast.dispatch as dispatch
    monkeypatch.setattr(dispatch, "dispatch_fast_job", lambda job_id, force, bg=None: "test-queue")
    monkeypatch.setattr("app.services.prefetch._request_cancel", lambda job_id: False)
    app = create_app()

    def override():
        db = S()
        try:
            yield db
            db.commit()
        finally:
            db.close()
    app.dependency_overrides[get_db] = override
    c = TestClient(app)
    c.S = S
    yield c


PDF = b"%PDF-1.4\n" + b"0" * 64


def _upload(c, prefetch):
    return c.post("/api/v1/tms/backload/pipeline/fast_ocr", data={"prefetch": "true" if prefetch else "false"},
                  files={"file": ("a.pdf", io.BytesIO(PDF), "application/pdf")})


def test_prefetch_commit_status_export_and_discard_rules(client):
    r = _upload(client, True)
    assert r.status_code == 202, r.text
    d = r.json()["data"]
    jid = d["job_id"]
    assert d["prefetch"] is True and d["events_url"].endswith(jid)

    # hidden everywhere people look
    assert client.get("/api/v1/processing/").json()["data"] == []
    assert client.get("/api/v1/dashboard/stats").json()["data"]["total_documents"] == 0

    # a result appears (the worker would write it); then Execute commits
    with client.S() as db:
        db.add(ProcessingResult(job_id=jid, confidence_json=[], ocr_metadata_json={}, extracted_json={
            "department": "TMS", "doc_type": "backload", "template_version": 1, "pages_processed": 1,
            "fields": {"iv_no": {"value": "20/4/MT/02", "confidence": 0.9},
                       "stn": {"value": "c/o 56 APO", "label": "Stn", "source": "discovered"}},
            "tables": [{"table_name": "Table_1", "table_index": 1, "page_number": 1,
                        "headers": ["S No", "Description"], "rows": [["1", "Gypsy"]], "cells": []}]}))
        job = db.get(ProcessingJob, jid); job.status = "COMPLETED"; db.commit()

    c = client.post(f"/api/v1/extract/jobs/{jid}/commit")
    assert c.status_code == 200 and c.json()["data"]["status"] == "COMPLETED"
    assert [j["job_id"] for j in client.get("/api/v1/processing/").json()["data"]] == [jid]

    # committed work cannot be discarded by a late call
    late = client.post(f"/api/v1/extract/jobs/{jid}/discard")
    assert late.status_code == 409 and late.json()["error"]["code"] == "PREFETCH_NOT_DISCARDABLE"

    # status carries the department JSON; so does the export route
    st = client.get(f"/api/v1/tms/backload/status/{jid}").json()["data"]
    assert st["export"]["fields"] == {"iv_no": "20/4/MT/02"} and st["export"]["additional_fields"] == {"stn": "c/o 56 APO"}
    assert st["export"]["tables"][0]["rows"] == [{"sno": "1", "description": "Gypsy"}]
    ex = client.get(f"/api/v1/extract/jobs/{jid}/export").json()["data"]
    assert ex["tables"][0]["columns"][0]["key"] == "sno"

    # an edit shows up in the next export
    client.post("/api/v1/extract/update-field", json={"job_id": jid, "field_key": "iv_no", "value": "20/4/MT/03"})
    assert client.get(f"/api/v1/extract/jobs/{jid}/export").json()["data"]["fields"]["iv_no"] == "20/4/MT/03"

    # found on document -> template field
    p = client.post(f"/api/v1/extract/jobs/{jid}/promote-field", json={"field_key": "stn", "name": "station"})
    assert p.status_code == 200, p.text
    ex = client.get(f"/api/v1/extract/jobs/{jid}/export").json()["data"]
    assert ex["fields"]["station"] == "c/o 56 APO" and ex["additional_fields"] == {}
    with client.S() as db:
        names = [f["name"] for f in db.query(Template).first().template_json["fields"]]
    assert names == ["iv_no", "station"]


def test_discard_then_commit_is_gone_and_a_normal_upload_is_visible(client):
    jid = _upload(client, True).json()["data"]["job_id"]
    with client.S() as db:
        db.get(ProcessingJob, jid).status = "FAILED"; db.commit()
    assert client.post(f"/api/v1/extract/jobs/{jid}/discard").json()["data"]["result"] == "purged"
    gone = client.post(f"/api/v1/extract/jobs/{jid}/commit")
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "PREFETCH_GONE"

    plain = _upload(client, False).json()["data"]
    assert plain["prefetch"] is False
    assert [j["job_id"] for j in client.get("/api/v1/processing/").json()["data"]] == [plain["job_id"]]


def test_prefetch_switched_off_on_the_server_gives_a_normal_job(client, monkeypatch):
    monkeypatch.setattr(settings, "PREFETCH_ENABLED", False)
    d = _upload(client, True).json()["data"]
    assert d["prefetch"] is False
    assert [j["job_id"] for j in client.get("/api/v1/processing/").json()["data"]] == [d["job_id"]]
