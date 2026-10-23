import pytest

from app.fast.engine import FastEngine
from app.fast.events import EventBus
from app.fast.ocr_cache import OcrCache
from app.fast.reader import PageReader
from app.fast.structure import build_blocks
from app.fast.triage import DocumentSource
from tests.unit._fast_helpers import make_digital_pdf


@pytest.fixture()
def run(tmp_path):
    def _run(fields):
        src = DocumentSource(make_digital_pdf(str(tmp_path / "d.pdf")), "application/pdf")
        bus = EventBus("job1", redis_conn=None)
        bus._redis = None
        eng = FastEngine(fields, bus, want_tables=True)
        eng.run_pages(src.page_count, PageReader(src, OcrCache(tmp_path / "c", True)).read)
        out = eng.finalize_fields()
        tables = eng.collect_tables()
        return eng, bus, out, tables
    return _run


def test_digital_pdf_fields_are_matched_and_validated(run):
    eng, bus, fields, _ = run([{"name": "name"}, {"name": "date"}, {"name": "ref_no"}, {"name": "missing_one"}])
    by = {f.field_name: f for f in fields}
    assert by["name"].value == "Rajesh Kumar"
    assert by["date"].value == "12 Jun 2026" and eng.extractor.meta["date"].state == "validated"
    assert by["ref_no"].value == "IV/2041"
    assert eng.extractor.meta["missing_one"].state == "failed"
    assert eng.pages[1].source == "native"          # no OCR ran


def test_events_come_in_order_and_fields_stream_before_the_end(run):
    eng, bus, fields, tables = run([{"name": "name"}, {"name": "date"}])
    eng.extractor.emit_final_events(fields)
    types = [e["type"] for e in bus.history]
    assert types.index("page_started") < types.index("field_extracted") < types.index("page_completed")
    assert types.index("page_completed") < types.index("field_validated")
    assert [e["seq"] for e in bus.history] == list(range(1, len(bus.history) + 1))
    first = next(e for e in bus.history if e["type"] == "field_extracted")
    assert first["v"] == 1 and first["data"]["provisional"] is True and first["data"]["source"] == "kv_pair"
    assert "first_field_ms" in eng.timings


def test_ruled_table_is_read_from_geometry(run):
    eng, bus, _, tables = run([])
    assert len(tables) == 1
    t = tables[0]
    assert t.headers == ["Item", "Qty", "Price"]
    assert t.rows == [["Pen", "2", "10.50"], ["Book", "1", "99.00"], ["Bag", "3", "450.00"]]
    assert t.extraction_method == "grid_lines"
    assert all(c["bbox"] for c in t.cells) and {c["row"] for c in t.cells} == {0, 1, 2, 3}
    assert any(e["type"] == "table_extracted" for e in bus.history)


def test_discovered_pairs_do_not_duplicate_template_fields(run):
    eng, _, fields, _ = run([{"name": "name"}])
    discovered = [f for f in fields if f.source == "discovered"]
    assert {f.label for f in discovered} >= {"Date", "Ref No"}
    assert "Name" not in {f.label for f in discovered}


def test_blocks_are_built_in_reading_order(run):
    eng, *_ = run([])
    blocks = build_blocks(eng.pages[1])
    assert blocks and blocks[0]["text"].startswith("Name")
    assert all(b["page"] == 1 for b in blocks)


def test_cancel_stops_the_job(tmp_path):
    from app.fast.engine import JobCancelled
    src = DocumentSource(make_digital_pdf(str(tmp_path / "d.pdf")), "application/pdf")
    bus = EventBus("job2", redis_conn=None)
    bus._redis = None
    bus._cancelled = True
    eng = FastEngine([{"name": "name"}], bus)
    with pytest.raises(JobCancelled):
        eng.run_pages(1, PageReader(src, OcrCache(tmp_path / "c", True)).read)


def test_verified_anchor_is_read_by_fast_engine(tmp_path):
    """fast_ocr must honour a saved box too (it used to ignore anchors entirely)."""
    from app.anchors import merge_anchor_fields
    src = DocumentSource(make_digital_pdf(str(tmp_path / "d.pdf")), "application/pdf")
    bus = EventBus("job-a", redis_conn=None)
    bus._redis = None
    plain = FastEngine([{"name": "ref_no"}], bus)
    plain.run_pages(src.page_count, PageReader(src, OcrCache(tmp_path / "c", True)).read)
    ref = {f.field_name: f for f in plain.finalize_fields()}["ref_no"]
    assert ref.value == "IV/2041" and ref.bbox

    # the user "re-boxes" the NAME value as ref_no: next run must read that box
    name_fr = None
    bus2 = EventBus("job-b", redis_conn=None); bus2._redis = None
    e2 = FastEngine([{"name": "name"}], bus2)
    e2.run_pages(src.page_count, PageReader(src, OcrCache(tmp_path / "c", True)).read)
    name_fr = {f.field_name: f for f in e2.finalize_fields()}["name"]
    pg = e2.pages[1]
    la, _ = merge_anchor_fields({}, {"ref_no": {"bbox": name_fr.bbox, "page_number": 1}},
                                page_w=pg.width, page_h=pg.height, verified=True)
    bus3 = EventBus("job-c", redis_conn=None); bus3._redis = None
    e3 = FastEngine([{"name": "ref_no"}], bus3, anchors=la)
    e3.run_pages(src.page_count, PageReader(src, OcrCache(tmp_path / "c", True)).read)
    got = {f.field_name: f for f in e3.finalize_fields()}["ref_no"]
    assert got.extraction_method == "anchor_verified"
    assert got.value == "Rajesh Kumar"
