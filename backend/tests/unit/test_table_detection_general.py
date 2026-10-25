"""Table detection on layouts nobody configured: scan edges, tilt, boxed forms, borderless tables.

Nothing here names a document type; every case is a layout property (page-edge rules, a tilted scan, a ruled form box,
a table with no rules at all).
"""
import fitz
import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.fast.engine import FastEngine
from app.fast.events import EventBus
from app.fast.ocr_cache import OcrCache
from app.fast.reader import PageReader
from app.fast.tables import _numeric_cell
from app.fast.triage import DocumentSource
from app.ocr.table_regions import find_table_regions


def _tables(path, tmp_path):
    src = DocumentSource(str(path), "application/pdf")
    bus = EventBus("job-detect", redis_conn=None)
    bus._redis = None
    eng = FastEngine([{"name": "x"}], bus, want_tables=True)
    eng.run_pages(src.page_count, PageReader(src, OcrCache(tmp_path / "c", True)).read)
    eng.finalize_fields()
    return eng.collect_tables()


def _ruled_table_image(w=1654, h=2339, frame=True, tilt=0.0) -> Image.Image:
    im = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(im)
    if frame:
        d.rectangle((12, 12, w - 14, h - 14), outline=0, width=4)          # the dark edge of a scanned page
    x0, x1, y0, rows, rh = 250, 1400, 700, 8, 70
    for i in range(rows + 1):
        d.line((x0, y0 + i * rh, x1, y0 + i * rh), fill=0, width=3)
    for x in (x0, 520, 800, 1100, x1):
        d.line((x, y0, x, y0 + rows * rh), fill=0, width=3)
    if tilt:
        im = im.rotate(tilt, resample=Image.BICUBIC, fillcolor=255)
    return im.convert("RGB")


def test_scan_edge_lines_do_not_swallow_the_table_into_a_page_sized_region():
    img = _ruled_table_image(frame=True)
    regions = find_table_regions(img)
    assert len(regions) == 1
    x1, y1, x2, y2 = regions[0]
    assert x2 - x1 < 0.85 * img.width and y2 - y1 < 0.5 * img.height
    assert 200 < x1 < 300 and 650 < y1 < 750


def test_a_tilted_scan_still_gives_one_table_region_of_the_right_size():
    img = _ruled_table_image(frame=True, tilt=1.2)
    regions = find_table_regions(img)
    assert len(regions) == 1
    x1, y1, x2, y2 = regions[0]
    assert 900 < x2 - x1 < 1400 and 450 < y2 - y1 < 800


def test_a_page_with_no_table_has_no_region():
    im = Image.new("RGB", (1654, 2339), "white")
    ImageDraw.Draw(im).rectangle((12, 12, 1640, 2325), outline=0, width=4)
    assert find_table_regions(im) == []


def test_numeric_cells_are_amounts_and_dates_not_names_with_a_number():
    assert all(_numeric_cell(c) for c in ("1,204.50", "12/03/2025", "(450)", "7", "₹ 300"))
    assert not any(_numeric_cell(c) for c in ("155 BH", "5 AF Hosp", "Hex nuts M8", "", "--"))


def test_a_ruled_form_box_of_label_value_lines_is_not_a_table(tmp_path):
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.draw_rect(fitz.Rect(60, 150, 540, 430))
    labels = ["Name", "Rank", "Unit", "Date of birth", "Place", "Remarks"]
    for i, lab in enumerate(labels):
        y = 180 + i * 40
        pg.insert_text((72, y), f"{lab} :", fontsize=11)
        pg.insert_text((250, y), f"value {i} for {lab.lower()}", fontsize=11)
        pg.draw_line((60, y + 12), (540, y + 12))
    path = tmp_path / "form.pdf"
    doc.save(str(path))
    assert _tables(path, tmp_path) == []


def _borderless(path, rows, cols=(60, 230, 330, 430, 510), header=("Division", "Q1", "Q2", "Q3", "Total")):
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((60, 70), "Statement of account", fontsize=15)
    pg.insert_text((60, 100), "Prepared for the board. All figures are in thousands unless stated otherwise.", fontsize=10)
    y = 150
    for x, h in zip(cols, header):
        pg.insert_text((x, y), h, fontsize=10)
    for r in rows:
        y += 22
        for x, v in zip(cols, r):
            pg.insert_text((x, y), v, fontsize=10)
    pg.insert_text((60, y + 50), "Notes: figures for the last quarter are unaudited.", fontsize=10)
    doc.save(str(path))
    return path


BORDERLESS_ROWS = [["North", "1,204.50", "1,310.00", "1,415.25", "3,929.75"], ["South", "980.00", "1,020.40", "1,105.10", "3,105.50"],
                   ["East", "750.25", "801.00", "899.90", "2,451.15"], ["West", "1,500.00", "1,620.75", "1,710.00", "4,830.75"],
                   ["Central", "640.00", "655.50", "700.00", "1,995.50"]]


def test_a_table_with_no_rules_at_all_is_found_from_its_aligned_columns(tmp_path):
    tables = _tables(_borderless(tmp_path / "b.pdf", BORDERLESS_ROWS), tmp_path)
    assert len(tables) == 1
    t = tables[0]
    assert t.headers == ["Division", "Q1", "Q2", "Q3", "Total"]
    assert len(t.rows) == 5 and t.rows[0][0] == "North" and t.rows[-1][-1] == "1,995.50"


def test_a_distribution_list_in_columns_is_not_taken_for_a_table(tmp_path):
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((60, 100), "Copy to:", fontsize=11)
    names = [["HQ Delhi Area", "HQ UB Area", "HQ MG Area"], ["HQ 4 Corps", "HQ 11 Corps", "HQ 12 Corps"],
             ["HQ 14 Corps", "HQ 15 Corps", "HQ 33 Corps"], ["Army Hospital", "Base Hospital", "INHS Asvini"],
             ["MH Meerut", "MH Ahmedabad", "92 BH"], ["155 BH", "158 BH", "153 GH"]]
    for i, row in enumerate(names):
        for x, v in zip((60, 230, 400), row):
            pg.insert_text((x, 140 + i * 16), v, fontsize=10)
    path = tmp_path / "dist.pdf"
    doc.save(str(path))
    assert _tables(path, tmp_path) == []


def test_prose_is_not_a_table(tmp_path):
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    text = ("The committee reviewed the arrangements described above and approved them without change. " * 6)
    pg.insert_textbox(fitz.Rect(60, 100, 540, 400), text, fontsize=11)
    path = tmp_path / "prose.pdf"
    doc.save(str(path))
    assert _tables(path, tmp_path) == []
