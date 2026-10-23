"""Table detection must work on any document: structure, not vocabulary."""
from types import SimpleNamespace

from app.fast.tables import _split_merged_header, looks_like_form_block
from app.pipelines.base import TableResult


def W(text, x, y, w=60, h=20):
    return SimpleNamespace(text=text, bbox=SimpleNamespace(x1=x, y1=y, x2=x + w, y2=y + h))


COLS = [0, 150, 300, 450]


def test_header_and_first_data_row_in_one_rule_row_are_split():
    words = [W("Product", 20, 10), W("Colour", 170, 10), W("Stock", 320, 10),
             W("Widget", 20, 50), W("Red", 170, 50), W("120", 320, 50)]
    rows = _split_merged_header([0, 120, 200], COLS, words, 0, 0, 20)
    assert len(rows) == 4 and 30 < rows[1] < 50


def test_a_cell_wrapped_onto_two_lines_is_not_split():
    words = [W("Item", 20, 10), W("Qty", 170, 10), W("Remarks", 320, 10),
             W("Rifle", 20, 10 + 40), W("01", 170, 50), W("(One)", 170, 75), W("R4", 320, 50)]
    # the data row's qty wraps ("01" / "(One)") but line 2 only touches one column
    out = _split_merged_header([0, 60, 130], COLS, words[:3], 0, 0, 20)
    assert out == [0, 60, 130]                      # header alone: nothing to split
    wrapped = [W("Ser", 20, 10), W("Qty", 170, 10), W("01", 170, 40), W("(One)", 170, 65)]
    assert _split_merged_header([0, 120, 200], COLS, wrapped, 0, 0, 20) == [0, 120, 200]


def test_names_over_names_are_not_split():
    words = [W("Rajesh", 20, 10), W("Delhi", 170, 10), W("Amit", 20, 50), W("Agra", 170, 50)]
    assert _split_merged_header([0, 120, 200], COLS, words, 0, 0, 20) == [0, 120, 200]


def test_boxed_form_paragraphs_are_not_returned_as_a_table():
    blob = {"text": "Make, Model and type : M/Gypsy B A Number : 10B 106915H Distance/Hours run : 89500 KM Holding unit : Stn HQ Maint Wksp: 261 Date of Inspection : 26 Mar 2026"}
    t = TableResult(table_index=1, page_number=1, headers=["a", "b"], rows=[["x", "y"]], cells=[blob, dict(blob), dict(blob)])
    assert looks_like_form_block(t)
    real = TableResult(table_index=1, page_number=1, headers=["Item", "Qty"], rows=[["Pen", "2"]],
                       cells=[{"text": "Pen"}, {"text": "2"}])
    assert not looks_like_form_block(real)


def test_two_column_checklist_of_label_value_cells_is_a_form_not_a_table():
    cells = [{"text": t} for t in ["(a) Maintenance: Adequate", "Engine No: G13BBN538681", "(b) Lubrication : Adequate",
                                   "Chassis No: MAS5EG", "(c) Cleanliness : Adequate", "Rotary Pump No : -"]]
    t = TableResult(table_index=1, page_number=1, headers=["Make: Gypsy", "Holding unit: Stn HQ"], rows=[["x", "y"]], cells=cells)
    assert looks_like_form_block(t)
    data = [{"text": x} for x in ["Pen", "2", "Book", "1", "Bag", "3"]]
    assert not looks_like_form_block(TableResult(table_index=1, page_number=1, headers=["Item", "Qty"], rows=[], cells=data))
