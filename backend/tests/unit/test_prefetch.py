"""Prefetch lifecycle on an in-memory SQLite DB: hidden from lists, commit, discard (and its refusal), reaper."""
import datetime
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401
from app.core.exceptions import DocIntError
from app.db.database import Base
from app.db.models import Department, Document, DocumentPage, ProcessingEvent, ProcessingJob, ProcessingResult, Template
from app.services import prefetch


@pytest.fixture()
def env(tmp_path):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng, expire_on_commit=False)()
    dept = Department(slug="TEST", name="Test"); db.add(dept); db.flush()
    tmpl = Template(department_id=dept.id, code="t", version=1, template_json={"fields": []}, is_active=1)
    db.add(tmpl); db.flush()

    def make(error_code, status="PROCESSING", age_s=0):
        f = tmp_path / f"{uuid.uuid4()}.pdf"; f.write_bytes(b"%PDF-1.4")
        doc = Document(id=str(uuid.uuid4()), original_filename="a.pdf", stored_filename=f.name, file_path=str(f),
                       file_type="application/pdf", file_size_bytes=8, template_id=tmpl.id, status="PROCESSING")
        db.add(doc); db.flush()
        job = ProcessingJob(id=str(uuid.uuid4()), document_id=doc.id, template_id=tmpl.id, template_version=1,
                            status=status, pipeline_mode="fast_ocr", error_code=error_code,
                            created_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_s))
        db.add(job); db.flush()
        db.add(DocumentPage(document_id=doc.id, page_number=1, width=10, height=10))
        db.add(ProcessingEvent(job_id=job.id, event_type="X", status="DONE"))
        db.commit()
        return job, f
    yield {"db": db, "make": make}
    db.close()


def test_hidden_from_lists_until_commit(env):
    db, make = env["db"], env["make"]
    pre, _ = make(prefetch.MARKER)
    plain, _ = make(None)
    from app.repositories.document_repo import DocumentRepository
    from app.repositories.job_repo import JobRepository
    assert {j.id for j in JobRepository(db).list_all()} == {plain.id}
    assert len(DocumentRepository(db).list_all()) == 1
    prefetch.commit(db, pre.id)
    assert {j.id for j in JobRepository(db).list_all()} == {plain.id, pre.id}


def test_discard_removes_rows_and_file_when_finished(env):
    db, make = env["db"], env["make"]
    job, f = make(prefetch.MARKER, status="COMPLETED")
    assert prefetch.discard(db, job.id) == "purged"
    assert db.get(ProcessingJob, job.id) is None and not f.exists()
    assert db.execute(select(ProcessingEvent)).first() is None


def test_discard_of_running_job_waits_for_the_reaper(env):
    db, make = env["db"], env["make"]
    job, f = make(prefetch.MARKER, status="PROCESSING")
    assert prefetch.discard(db, job.id) == "discarding"
    assert db.get(ProcessingJob, job.id).error_code == prefetch.DISCARDED and f.exists()
    job.status = "FAILED"; db.commit()                       # the worker stopped (cancelled)
    assert prefetch.reap(db) == 1
    assert db.get(ProcessingJob, job.id) is None and not f.exists()


def test_discard_refuses_a_committed_job(env):
    db, make = env["db"], env["make"]
    job, f = make(None, status="COMPLETED")
    with pytest.raises(DocIntError) as e:
        prefetch.discard(db, job.id)
    assert e.value.code == "PREFETCH_NOT_DISCARDABLE"
    assert db.get(ProcessingJob, job.id) is not None and f.exists()


def test_commit_of_a_discarded_or_missing_prefetch_is_gone(env):
    db, make = env["db"], env["make"]
    job, _ = make(prefetch.DISCARDED)
    for jid in (job.id, "nope"):
        with pytest.raises(DocIntError):
            prefetch.commit(db, jid)


def test_reaper_discards_an_abandoned_prefetch_but_not_a_fresh_one(env):
    db, make = env["db"], env["make"]
    old, _ = make(prefetch.MARKER, status="COMPLETED", age_s=3600)
    fresh, _ = make(prefetch.MARKER, status="COMPLETED", age_s=5)
    prefetch.reap(db)
    assert db.get(ProcessingJob, old.id) is None
    assert db.get(ProcessingJob, fresh.id) is not None


def test_commit_is_idempotent_for_an_ordinary_job(env):
    db, make = env["db"], env["make"]
    job, _ = make(None, status="COMPLETED")
    assert prefetch.commit(db, job.id).id == job.id


def test_prefetch_upload_creates_a_hidden_job_and_a_normal_upload_a_visible_one(env, tmp_path, monkeypatch):
    import io

    from starlette.datastructures import UploadFile

    from app.api.routes import extract as ex
    from app.core.config import settings
    from app.repositories.job_repo import JobRepository

    db = env["db"]
    monkeypatch.setattr(type(settings), "storage_path", property(lambda self: tmp_path), raising=False)
    tmpl = db.execute(select(Template)).scalars().first()
    pdf = b"%PDF-1.4\n" + b"0" * 64

    def up():
        return UploadFile(file=io.BytesIO(pdf), filename="x.pdf", size=len(pdf))

    _, hidden_id = ex._save_uploaded_file_and_create_job(db, up(), tmpl, pipeline_mode="fast_ocr", prefetch=True)
    _, plain_id = ex._save_uploaded_file_and_create_job(db, up(), tmpl, pipeline_mode="fast_ocr")
    assert db.get(ProcessingJob, hidden_id).error_code == prefetch.MARKER
    assert db.get(ProcessingJob, plain_id).error_code is None
    assert {j.id for j in JobRepository(db).list_all()} == {plain_id}
    prefetch.commit(db, hidden_id)
    assert {j.id for j in JobRepository(db).list_all()} == {plain_id, hidden_id}
