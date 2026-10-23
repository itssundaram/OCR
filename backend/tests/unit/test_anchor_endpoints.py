"""Lock / save-field / re-run endpoints and the stuck-job reaper, on an in-memory SQLite DB
(the endpoint functions are called directly; no Oracle, Redis or HTTP server)."""
import datetime
import uuid

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401
from app.core.exceptions import DocIntError
from app.db.database import Base
from app.db.models import Department, Document, ProcessingJob, ProcessingResult, Template


@pytest.fixture()
def env(tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    db = S()
    dept = Department(slug="TEST", name="Test")
    db.add(dept); db.flush()
    tmpl = Template(department_id=dept.id, code="vehicle", version=1, template_json={"fields": []}, is_active=1)
    db.add(tmpl); db.flush()
    f = tmp_path / "doc.pdf"; f.write_bytes(b"%PDF-1.4 test")
    doc = Document(id=str(uuid.uuid4()), original_filename="doc.pdf", stored_filename="doc.pdf", file_path=str(f),
                   file_type="application/pdf", file_size_bytes=13, template_id=tmpl.id, status="COMPLETED")
    db.add(doc); db.flush()
    job = ProcessingJob(id=str(uuid.uuid4()), document_id=doc.id, template_id=tmpl.id, template_version=1,
                        status="COMPLETED", pipeline_mode="learned_anchor")
    db.add(job)

    def D(t, x1, x2, y1=300, y2=330):
        return {"text": t, "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}, "confidence": 0.9, "page_number": 1}
    dets = [D("Engine", 100, 170), D("No", 175, 210), D(":", 212, 220), D("WD4", 240, 300),
            D("-", 305, 315), D("299332", 320, 420),
            D("Chassis", 100, 180, 360, 390), D("No", 185, 215, 360, 390), D("MA5ABC123456", 240, 460, 360, 390)]
    db.add(ProcessingResult(job_id=job.id, extracted_json={"fields": {}}, confidence_json=[], ocr_metadata_json={
        "all_detections": dets, "doc_page_width": 1000, "doc_page_height": 1400}))
    db.commit()
    yield {"db": db, "S": S, "tmpl": tmpl, "job": job, "doc": doc}
    db.close()


ENGINE_BOX = {"x1": 235, "y1": 295, "x2": 430, "y2": 335}
CHASSIS_BOX = {"x1": 235, "y1": 355, "x2": 470, "y2": 395}


def test_lock_merges_learns_label_and_versions(env):
    from app.api.routes.templates import LockAnchorsRequest, lock_template_anchors
    db, t, j = env["db"], env["tmpl"], env["job"]
    r1 = lock_template_anchors(t.id, LockAnchorsRequest(
        fields={"engine_no": {"bbox": ENGINE_BOX, "page_number": 1}}, page_width=1000, page_height=1400, job_id=j.id), db)
    la1 = r1.data["learned_anchors"]
    assert la1["version"] == 1 and la1["is_verified"] is True
    assert la1["fields"]["engine_no"]["label_text"].lower().startswith("engine no")
    assert la1["fields"]["engine_no"]["value"] == "WD4 - 299332"

    r2 = lock_template_anchors(t.id, LockAnchorsRequest(
        fields={"chasis_no": {"bbox": CHASSIS_BOX, "page_number": 1}}, page_width=1000, page_height=1400, job_id=j.id), db)
    la2 = r2.data["learned_anchors"]
    assert set(la2["fields"]) == {"engine_no", "chasis_no"}          # earlier field NOT dropped
    assert la2["version"] == 2 and r2.data["report"]["kept"] == ["engine_no"]

    with pytest.raises(DocIntError) as ei:
        lock_template_anchors(t.id, LockAnchorsRequest(fields={"x": {"bbox": ENGINE_BOX}}, expected_version=1), db)
    assert ei.value.code == "ANCHOR_VERSION_CONFLICT"


def test_manage_field_uses_same_store_and_remove(env):
    from app.api.routes.extract import ManageTemplateFieldsRequest, manage_template_field
    from app.api.routes.templates import rollback_template_anchors
    db, t, j = env["db"], env["tmpl"], env["job"]
    manage_template_field(ManageTemplateFieldsRequest(
        job_id=j.id, action="add", field_key="engine_no", value="WD4 - 299332", bbox=ENGINE_BOX, page_number=1), db)
    db.refresh(t)
    a = t.template_json["learned_anchors"]["fields"]["engine_no"]
    assert a["source"] == "user_field" and "rel_bbox" in a and a["label_text"].lower().startswith("engine")
    manage_template_field(ManageTemplateFieldsRequest(
        job_id=j.id, action="add", field_key="chasis_no", value="MA5ABC123456", bbox=CHASSIS_BOX, page_number=1), db)
    db.refresh(t)
    assert set(t.template_json["learned_anchors"]["fields"]) == {"engine_no", "chasis_no"}
    manage_template_field(ManageTemplateFieldsRequest(job_id=j.id, action="remove", field_key="chasis_no"), db)
    db.refresh(t)
    assert set(t.template_json["learned_anchors"]["fields"]) == {"engine_no"}
    out = rollback_template_anchors(t.id, None, db)       # undo the removal
    db.refresh(t)
    assert set(t.template_json["learned_anchors"]["fields"]) == {"engine_no", "chasis_no"}
    assert out.data["field_count"] == 2


def test_rerun_creates_new_job_on_same_file(env, monkeypatch):
    import app.api.routes.extract as ex
    db, j, d = env["db"], env["job"], env["doc"]
    calls = []
    monkeypatch.setattr(ex, "_dispatch_job", lambda path, jid, args, bg, fn: calls.append((path, jid, args)) or "redis")
    out = ex.rerun_job(j.id, BackgroundTasks(), ex.RerunJobRequest(force_relearn=False), db)
    new = db.get(ProcessingJob, out.data["job_id"])
    assert new.id != j.id and new.pipeline_mode == "learned_anchor" and new.status == "QUEUED"
    new_doc = db.get(Document, new.document_id)
    assert new_doc.id != d.id and new_doc.file_path == d.file_path
    assert calls == [("app.workers.tasks.run_single_pipeline_job", new.id, ("learned_anchor", False))]
    assert db.get(ProcessingJob, j.id).status == "COMPLETED"           # the old result is untouched


def test_rerun_errors(env, monkeypatch):
    import os
    import app.api.routes.extract as ex
    db, j, d = env["db"], env["job"], env["doc"]
    monkeypatch.setattr(ex, "_dispatch_job", lambda *a, **k: "redis")
    j.pipeline_mode = "orchestration"; db.commit()
    with pytest.raises(DocIntError) as ei:
        ex.rerun_job(j.id, BackgroundTasks(), None, db)
    assert ei.value.code == "RERUN_UNSUPPORTED"
    j.pipeline_mode = "learned_anchor"; db.commit()
    os.remove(d.file_path)
    with pytest.raises(DocIntError) as ei:
        ex.rerun_job(j.id, BackgroundTasks(), None, db)
    assert ei.value.code == "FILE_NOT_FOUND"
    with pytest.raises(DocIntError):
        ex.rerun_job("nope", BackgroundTasks(), None, db)


def test_reaper_only_fails_jobs_past_their_timeout(env, monkeypatch):
    import app.workers.reaper as rp
    import app.services.extractor as extractor
    db, S, j = env["db"], env["S"], env["job"]
    now = datetime.datetime.now(datetime.timezone.utc)
    j.status, j.started_at = "PROCESSING", now - datetime.timedelta(hours=2)
    fresh = ProcessingJob(id=str(uuid.uuid4()), document_id=j.document_id, template_id=j.template_id, template_version=1,
                          status="PROCESSING", started_at=now - datetime.timedelta(seconds=30))
    db.add(fresh); db.commit()
    failed = []
    monkeypatch.setattr(rp, "SessionLocal", S)
    monkeypatch.setattr(extractor, "mark_job_permanently_failed", lambda jid, msg: failed.append(jid))
    assert rp.reap_stuck_jobs() == 1
    assert failed == [j.id]
