"""Table understanding: header / title / footer, multi-page join, column types, per-cell checks."""
import numpy as np
from PIL import Image, ImageDraw

from app.fast.tableschema import finalize_tables, infer_column, recount_quality, validate_cell
from tests.unit._table_helpers import HEADER, body, pages, raw_table, two_page_tables


def test_two_pages_become_one_table_with_header_and_count_check():
    raw, pg = two_page_tables()
    out = finalize_tables(raw, pg, None)
    assert len(out) == 1
    t = out[0]
    assert t.headers == HEADER and len(t.rows) == 10
    assert t.meta["pages"] == [1, 2]
    assert "LIST OF CADETS" in t.meta["title_text"]
    assert "TOTAL" in t.meta["footer_text"]
    cc = t.meta["quality"]["count_check"]
    assert cc["expected"] == 10 and cc["found"] == 10 and cc["ok"] is True
    assert t.meta["quality"]["serial"] == {"first": 1, "last": 10, "gaps": []}
    kinds = {c["name"]: c["kind"] for c in t.meta["quality"]["columns"]}
    assert kinds["S No"] == "serial" and kinds["DoB"] == "date" and kinds["Service"] == "enum"
    assert {c["page"] for c in t.cells} == {1, 2}


def test_count_check_fails_when_rows_are_missing():
    raw, pg = two_page_tables()
    raw[1].rows.pop(0)                                   # lose serial 7 on page 2
    raw[1].cells = [c for c in raw[1].cells if c["row"] != 2]
    for c in raw[1].cells:
        if c["row"] > 2:
            c["row"] -= 1
    out = finalize_tables(raw, pg, None)[0]
    cc = out.meta["quality"]["count_check"]
    assert cc["ok"] is False and cc["found"] == 9
    assert any("Document says 10" in n for n in out.meta["quality"]["notes"])


def test_tables_on_non_adjacent_or_misaligned_pages_stay_separate():
    a = raw_table(1, [HEADER] + body(1, 4), 1)
    b = raw_table(3, [HEADER] + body(5, 8), 2)           # page 3, not 2
    assert len(finalize_tables([a, b], pages(1, 3), None)) == 2


def test_serial_misreads_are_fixed_from_the_sequence_and_the_original_is_kept():
    rows = [HEADER] + body(1, 6)
    rows[2][0] = "4 2."          # 2 read badly
    rows[4][0] = "14"            # 4 read as 14
    out = finalize_tables([raw_table(1, rows)], pages(1), None)[0]
    assert [r[0] for r in out.rows] == ["1", "2", "3", "4", "5", "6"]
    fixed = [c for c in out.cells if c["col"] == 0 and c["row"] == 2][0]
    assert fixed["raw_text"] == "4 2." and fixed["state"] == "accepted"


def test_invalid_date_is_flagged_with_a_safe_suggestion_only():
    spec = {"kind": "date", "year_median": 2003}
    v = validate_cell(spec, "12-70-2003", 0.9)
    assert v.state == "flagged" and v.suggestion == "12-10-2003" and v.text == "12-70-2003"
    amb = validate_cell(spec, "99-99-2003", 0.9)
    assert amb.state == "flagged" and amb.suggestion is None      # no unique candidate -> no guess


def test_enum_cleans_edge_junk_but_never_invents():
    spec = infer_column(0, "Service", ["ARMY", "ARMY", "NAVY", "NAVY", "ARMY", "AIRFORCE", "AIRFORCE", "ARMY"])
    assert spec["kind"] == "enum"
    ok = validate_cell(spec, "ARMY -", 0.4)
    assert ok.state == "accepted" and ok.text == "ARMY"
    off = validate_cell(spec, "ZEBRA", 0.9)
    assert off.state == "flagged" and off.suggestion is None


def test_id_shape_suggestion_requires_a_unique_candidate():
    spec = {"kind": "id", "shape": "AA-99999A"}
    v = validate_cell(spec, "MS-23050xX", 0.9)
    assert v.state == "flagged" and v.suggestion == "MS-23050X"
    good = validate_cell(spec, "ms-23051a", 0.9)
    assert good.state == "accepted" and good.text == "MS-23051A"


def test_names_with_digits_or_joined_words_are_flagged():
    spec = {"kind": "name"}
    assert validate_cell(spec, "RAHUL SHARMA", 0.9).state == "accepted"
    assert validate_cell(spec, "R4HUL SH4RMA", 0.9).state == "flagged"
    assert validate_cell(spec, "ABHISHEKKUMARSINGH", 0.9).state == "flagged"
    assert validate_cell(spec, "a RAMA LAKSHMI", 0.9).state == "flagged"


def test_unreadable_script_column_is_marked_not_guessed():
    rows = [HEADER] + body(1, 10)
    junk = ["xq zt", "Aaa sma", "UnidtdRex", "SHG HUS", "faa fig", "Agave", "wad", "feria fie", "Vieamed", "Sh AMT"]
    for r, j in zip(rows[1:], junk):
        r[1] = j
    raw = raw_table(1, rows, conf=0.2)
    out = finalize_tables([raw], pages(1), None)[0]
    assert out.meta["columns"][1]["kind"] == "nonlatin"
    cell = [c for c in out.cells if c["col"] == 1 and c["row"] == 1][0]
    assert cell["state"] == "flagged" and cell["reasons"][0]["code"] == "non_latin"


def test_user_edit_updates_cell_and_summary():
    from app.services.table_edit import apply_cell_edit
    rows = [HEADER] + body(1, 6)
    rows[3][2] = "40-08-2003"
    out = finalize_tables([raw_table(1, rows)], pages(1), None)[0]
    d = {"table_index": 1, "headers": out.headers, "rows": out.rows, "cells": out.cells, "meta": out.meta, "markdown": ""}
    before = d["meta"]["quality"]["flagged"]
    cell = [c for c in d["cells"] if c["row"] == 3 and c["col"] == 2][0]
    assert cell["state"] == "flagged" and before >= 1
    upd = apply_cell_edit(d, 3, 2, "10-08-2003")
    c2 = [c for c in upd["cells"] if c["row"] == 3 and c["col"] == 2][0]
    assert c2["state"] == "accepted" and c2["edited"] and c2["rev"] == 1 and c2["reasons"] == []
    assert upd["meta"]["quality"]["flagged"] == before - 1
    assert upd["rows"][2][2] == "10-08-2003"


def test_ruling_lines_are_erased_for_ocr_and_boxes_map_back():
    from app.fast.linefree import PrepInfo, prepare_for_ocr, restore_lines
    from app.ocr.base import BoundingBox, OCRLine, OCRWord
    img = Image.new("RGB", (1200, 1600), "white")
    d = ImageDraw.Draw(img)
    for y in range(200, 1400, 100):
        d.line([(100, y), (1100, y)], fill="black", width=3)
    for x in range(100, 1101, 200):
        d.line([(x, 200), (x, 1300)], fill="black", width=3)
    out, info = prepare_for_ocr(img)
    assert info.changed and info.regions >= 1
    arr = np.array(out.convert("L"))
    assert (arr[300, 120:1080] < 128).sum() == 0                  # the horizontal rule is gone
    plain, none = prepare_for_ocr(Image.new("RGB", (800, 800), "white"))
    assert not none.changed and plain.size == (800, 800)
    ln = OCRLine(text="a", words=[OCRWord(text="a", bbox=BoundingBox(x1=0, y1=0, x2=10, y2=10), confidence=0.9)])
    assert restore_lines([ln], PrepInfo(changed=True, skew_deg=0.0, size=(100, 100))) == [ln]


def test_glued_cell_text_is_split_at_the_rule():
    from app.fast.tables import _split_word
    assert _split_word("8/MS-23057F", 0, 110, 20) == ("8", "MS-23057F")
    assert _split_word("COMMISSION", 0, 100, 50) is None            # no separator: never cut a word
