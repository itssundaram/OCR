"""A table with no grid (an invoice: a header rule, a closing rule, rows set in columns) on a layout nobody configured.

Everything here is derived from the page itself: the rules, the text lines and the values in each column. Nothing
names a document type, a column or a vocabulary.
"""
import fitz
import pytest
from PIL import Image

from app.fast.engine import FastEngine
from app.fast.events import EventBus
from app.fast.ocr_cache import OcrCache
from app.fast.reader import PageReader
from app.fast.triage import DocumentSource
from app.ocr.table_regions import find_table_regions


def _invoice(path: str, rows: list[list[str]], header=("Sl No", "Item Description", "Status", "Qty", "Rate", "Amount")) -> str:
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.insert_text((72, 70), "ACME TRADING CO.  -  TAX INVOICE", fontsize=15)
    pg.insert_text((72, 110), "Invoice No: INV-2026-0417", fontsize=11)
    pg.insert_text((330, 110), "Invoice Date: 14 Mar 2026", fontsize=11)
    cols, y = [72, 110, 260, 340, 410, 490], 200
    for x, h in zip(cols, header):
        pg.insert_text((x, y), h, fontsize=10)
    pg.draw_line((72, y + 6), (540, y + 6))
    for r in rows:
        y += 24
        for x, c in zip(cols, r):
            pg.insert_text((x, y), c, fontsize=10)
    pg.draw_line((72, y + 8), (540, y + 8))
    doc.save(path)
    doc.close()
    return path


ROWS = [["1", "Steel bolts M8", "Paid", "120", "4.50", "540.00"], ["2", "Hex nuts M8", "Paid", "120", "2.25", "270.00"],
        ["3", "Washers 8mm", "Pending", "300", "0.80", "240.00"], ["4", "Spring clamps", "Paid", "40", "12.00", "480.00"],
        ["5", "Cable ties", "Pnding", "500", "0.35", "175.00"], ["6", "Anchor plates", "Pending", "25", "31.00", "775.00"],
        ["7", "Threaded rod", "Paid", "60", "9.40", "564.00"]]


def _image(path: str) -> Image.Image:
    pix = fitz.open(path)[0].get_pixmap(dpi=200)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def test_header_rule_and_closing_rule_make_a_table_region_including_the_header_line(tmp_path):
    img = _image(_invoice(str(tmp_path / "i.pdf"), ROWS))
    regions = find_table_regions(img)
    assert len(regions) == 1
    x1, y1, x2, y2 = regions[0]
    scale = 200 / 72
    assert y1 < 195 * scale < y2 and y1 > 150 * scale        # the header line (above the first rule) is inside; the title is not
    assert x2 - x1 > 0.7 * img.width


def test_a_paragraph_between_two_rules_is_not_a_table(tmp_path):
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.draw_line((72, 200), (540, 200))
    text = ("This letter confirms that the arrangements described above were reviewed and approved by the committee. "
            "Nothing in it is a list, every line is running prose that wraps at the margin of the page like a paragraph.")
    pg.insert_textbox(fitz.Rect(72, 210, 540, 330), text * 2, fontsize=11)
    pg.draw_line((72, 340), (540, 340))
    path = str(tmp_path / "p.pdf")
    doc.save(path)
    assert find_table_regions(_image(path)) == []


def test_rules_of_different_width_are_not_a_table(tmp_path):
    doc = fitz.open()
    pg = doc.new_page(width=595, height=842)
    pg.draw_line((72, 200), (540, 200))
    for i in range(6):
        pg.insert_text((72, 230 + i * 24), f"line {i}", fontsize=10)
        pg.insert_text((300, 230 + i * 24), f"{i}", fontsize=10)
        pg.insert_text((450, 230 + i * 24), f"x{i}", fontsize=10)
    pg.draw_line((72, 400), (250, 400))                 # a signature line, nothing like the top rule
    path = str(tmp_path / "s.pdf")
    doc.save(path)
    assert find_table_regions(_image(path)) == []


@pytest.fixture()
def read_tables(tmp_path):
    def _go(rows):
        src = DocumentSource(_invoice(str(tmp_path / "i.pdf"), rows), "application/pdf")
        bus = EventBus("job-grid", redis_conn=None)
        bus._redis = None
        eng = FastEngine([{"name": "invoice_no"}], bus, want_tables=True)
        eng.run_pages(src.page_count, PageReader(src, OcrCache(tmp_path / "c", True)).read)
        eng.finalize_fields()
        return eng.collect_tables()
    return _go


def test_rows_are_the_text_lines_and_the_columns_are_the_gaps(read_tables):
    tables = read_tables(ROWS)
    assert len(tables) == 1
    t = tables[0]
    assert t.headers == ["Sl No", "Item Description", "Status", "Qty", "Rate", "Amount"]
    assert t.rows[0] == ["1", "Steel bolts M8", "Paid", "120", "4.50", "540.00"]
    assert len(t.rows) == 7 and t.rows[-1][1] == "Threaded rod"


def test_a_wrapped_line_belongs_to_the_row_above_it():
    from types import SimpleNamespace

    from app.fast.tables import _rows_from_text

    def W(text, x, y):
        return SimpleNamespace(text=text, bbox=SimpleNamespace(x1=x, y1=y, x2=x + 40, y2=y + 12))

    words = [W("Item", 0, 0), W("Qty", 100, 0),
             W("Bolt", 0, 30), W("4", 100, 30), W("(M8", 0, 43), W("zinc)", 50, 43),       # wrapped second line, tighter pitch
             W("Nut", 0, 70), W("9", 100, 70)]
    edges = _rows_from_text(words, 0, 100, 12)
    assert len(edges) - 1 == 3                       # header, "Bolt (M8 zinc)", "Nut" - not four rows
    assert _rows_from_text([W("only", 0, 0)], 0, 100, 12) == []


def test_an_unseen_enum_column_gets_a_suggestion_from_its_own_values_and_is_never_auto_applied(read_tables):
    from app.fast.tableschema import finalize_tables
    from types import SimpleNamespace

    raw = read_tables(ROWS)
    out = finalize_tables(raw, {1: SimpleNamespace(width=1654, height=2339, text="")}, None)
    t = out[0]
    kinds = {c["name"]: c["kind"] for c in t.meta["quality"]["columns"]}
    assert kinds["Status"] == "enum"        # inferred from the values, not from a name list
    bad = [c for c in t.cells if c["text"] == "Pnding"][0]
    assert bad.get("suggestion") == "Pending"                              # the closest value that column already uses
    assert bad["text"] == "Pnding"                                         # the reading itself is untouched
    assert all(not c.get("suggestion") for c in t.cells if c["text"] in ("Paid", "Pending"))
