"""Background cell refinement: choosing cells, merging safely, the slice runner."""
import uuid
from copy import deepcopy

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401
from app.db.database import Base
from app.db.models import Department, Document, ProcessingJob, ProcessingResult, Template
from app.fast.refine_cells import Change, apply_changes, pending_targets
from app.fast.tableschema import finalize_tables
from tests.unit._table_helpers import HEADER, body, pages, raw_table


def _table(mutate=None) -> dict:
    rows = [HEADER] + body(1, 10)
    rows[3][2] = "40-08-2003"          # invalid date  (row 3)
    rows[8][2] = "99.07.2003"          # invalid date  (row 8)
    rows[5][1] = "FARAH KHAN7"         # name with a digit (row 5)
    if mutate:
        mutate(rows)
    t = finalize_tables([raw_table(1, rows)], pages(1), None)[0]
    return {"table_index": 1, "page_number": 1, "headers": t.headers, "rows": t.rows, "cells": t.cells,
            "meta": t.meta, "markdown": ""}


def _flagged(table, row, col):
    return [c for c in table["cells"] if c["row"] == row and c["col"] == col][0]


def test_focus_rows_come_first_then_cheap_tier_then_reading_order():
    t = _table()
    plain = pending_targets([t], None, 0)
    assert [(x.row, x.col) for x in plain][:3] == [(3, 2), (5, 1), (8, 2)]
    focused = pending_targets([t], {"table_index": 1, "row_from": 8, "row_to": 10}, 0)
    assert (focused[0].row, focused[0].col) == (8, 2)
    assert all(x.tier == "t1" for x in plain)


def test_cells_already_tried_or_edited_are_skipped():
    t = _table()
    _flagged(t, 3, 2)["tried"] = ["t1"]
    _flagged(t, 5, 1)["edited"] = True
    got = [(x.row, x.col) for x in pending_targets([t], None, 0)]
    assert got == [(8, 2)]


def test_apply_changes_merges_and_never_overwrites_a_person():
    t = _table()
    tables = [t]
    cell = _flagged(t, 3, 2)
    ok = Change(1, 3, 2, cell.get("rev", 0), "10-08-2003", "accepted", 0.8, [], None, "t1", raw_text="10-08-2003")
    stale = Change(1, 5, 1, 0, "FARAH KHAN", "accepted", 0.8, [], None, "t1")
    _flagged(t, 5, 1).update({"edited": True, "rev": 1, "text": "FARAH K", "state": "accepted"})
    before = t["meta"]["quality"]["flagged"]
    applied = apply_changes(tables, [ok, stale])
    assert [(a.row, a.col) for a in applied] == [(3, 2)]
    assert _flagged(t, 3, 2)["text"] == "10-08-2003" and _flagged(t, 3, 2)["rev"] == 1
    assert _flagged(t, 3, 2)["raw_text"] == "40-08-2003"
    assert _flagged(t, 5, 1)["text"] == "FARAH K"                  # the person's edit stands
    assert t["rows"][2][2] == "10-08-2003" and "10-08-2003" in t["markdown"]
    assert t["meta"]["quality"]["flagged"] < before


# ── the slice runner on an in-memory DB with fake readers ────────────────────
@pytest.fixture()
def env(tmp_path, monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    with S() as db:
        dept = Department(slug="T", name="T"); db.add(dept); db.flush()
        tm = Template(department_id=dept.id, code="x", version=1, template_json={"fields": []}, is_active=1)
        db.add(tm); db.flush()
        f = tmp_path / "d.pdf"; f.write_bytes(b"%PDF")
        doc = Document(id=str(uuid.uuid4()), original_filename="d.pdf", stored_filename="d.pdf", file_path=str(f),
                       file_type="application/pdf", file_size_bytes=4, template_id=tm.id, status="COMPLETED")
        db.add(doc); db.flush()
        job = ProcessingJob(id=str(uuid.uuid4()), document_id=doc.id, template_id=tm.id, template_version=1,
                            status="COMPLETED", pipeline_mode="fast_ocr")
        db.add(job); db.flush()
        t = _table()
        db.add(ProcessingResult(job_id=job.id, extracted_json={"tables": [t]}, confidence_json=[],
                                ocr_metadata_json={"refine": {"status": "pending", "flagged_at_start": 3, "improved": 0,
                                                              "vlm_calls": 0, "slices": 0}}))
        db.commit()
        job_id = job.id

    import app.fast.refine as R
    monkeypatch.setattr(R, "SessionLocal", S)
    monkeypatch.setattr(R, "_redis", lambda: None)
    monkeypatch.setattr(R.settings, "FAST_VLM_FALLBACK_ENABLED", False)
    monkeypatch.setattr(R, "dispatch_refine", lambda *a, **k: None)

    class FakePages:
        def __init__(self, doc): pass
        def get(self, n): return Image.new("RGB", (1000, 1000), "white")
    monkeypatch.setattr(R, "_Pages", FakePages)

    truth = {(3, 2): "10-08-2003", (5, 1): "FARAH KHAN", (8, 2): "09-07-2003"}

    class FakeReader:
        def __init__(self): self.calls = 0
        def read(self, img, psm=7, whitelist=None):
            self.calls += 1
            return self._next, 0.9
    reader = FakeReader()
    import app.fast.refine_cells as RC

    def fake_refine(table, tgt, img, rd):
        reader._next = truth[(tgt.row, tgt.col)]
        return RC.refine_with_tesseract(table, tgt, img, reader)
    monkeypatch.setattr(R, "refine_with_tesseract", fake_refine)
    monkeypatch.setattr(R, "CropReader", lambda: reader)
    yield {"S": S, "job_id": job_id, "reader": reader}


def test_slice_reads_flagged_cells_saves_and_finishes(env):
    from app.fast.refine import run_refine_slice
    run_refine_slice(env["job_id"])
    with env["S"]() as db:
        row = db.query(ProcessingResult).filter_by(job_id=env["job_id"]).first()
        t = row.extracted_json["tables"][0]
        ref = row.ocr_metadata_json["refine"]
    assert ref["status"] == "done" and ref["improved"] == 3
    assert t["rows"][2][2] == "10-08-2003" and t["rows"][4][1] == "FARAH KHAN" and t["rows"][7][2] == "09-07-2003"
    assert t["meta"]["quality"]["flagged"] == 0
    assert _flagged(t, 3, 2)["reasons"][-1]["code"] == "re_read"


def test_person_edit_made_during_refinement_is_not_overwritten(env, monkeypatch):
    import app.fast.refine as R
    from app.services.table_edit import apply_cell_edit
    orig = R.refine_with_tesseract

    def edit_then_read(table, tgt, img, rd):
        if (tgt.row, tgt.col) == (3, 2):                      # the person fixes it while the batch is being computed
            with env["S"]() as db:
                row = db.query(ProcessingResult).filter_by(job_id=env["job_id"]).first()
                ej = dict(row.extracted_json)
                ej["tables"] = [apply_cell_edit(ej["tables"][0], 3, 2, "11-08-2003")]
                row.extracted_json = ej
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(row, "extracted_json")
                db.commit()
        return orig(table, tgt, img, rd)
    monkeypatch.setattr(R, "refine_with_tesseract", edit_then_read)
    R.run_refine_slice(env["job_id"])
    with env["S"]() as db:
        t = db.query(ProcessingResult).filter_by(job_id=env["job_id"]).first().extracted_json["tables"][0]
    assert t["rows"][2][2] == "11-08-2003"                       # the person's value, not the machine's
    assert _flagged(t, 3, 2)["edited"] is True


def test_cancel_stops_before_any_work(env, monkeypatch):
    import app.fast.refine as R
    monkeypatch.setattr(R, "_cancelled", lambda job_id: True)
    R.run_refine_slice(env["job_id"])
    assert env["reader"].calls == 0
    with env["S"]() as db:
        ref = db.query(ProcessingResult).filter_by(job_id=env["job_id"]).first().ocr_metadata_json["refine"]
    assert ref["status"] == "cancelled"


def test_job_completed_only_ends_the_stream_when_no_refinement_follows():
    from app.fast.events import EventBus, is_terminal
    assert is_terminal({"type": "job_completed", "data": {"refine": False}})
    assert not is_terminal({"type": "job_completed", "data": {"refine": True}})
    assert is_terminal({"type": "refine_completed", "data": {}})
    assert is_terminal({"type": "job_failed", "data": {}})
    assert not is_terminal({"type": "cell_refined", "data": {}})
    bus = EventBus("j1", redis_conn=None, seq_start=40)
    assert bus.emit("refine_progress")["seq"] == 41


def test_cell_closeup_is_cut_from_the_page_at_the_cell_box(env, monkeypatch):
    import io
    import app.api.routes.refine as RT
    monkeypatch.setattr(RT, "_page_image", lambda path, mime, page: Image.new("RGB", (1000, 1000), "white"))
    with env["S"]() as db:
        resp = RT.get_cell_image(env["job_id"], 1, 3, 2, 10, db)
        img = Image.open(io.BytesIO(resp.body))
        assert resp.media_type == "image/png" and img.width == (700 - 500) + 20          # col 2 box + padding both sides
        with pytest.raises(Exception):
            RT.get_cell_image(env["job_id"], 1, 99, 99, 10, db)
