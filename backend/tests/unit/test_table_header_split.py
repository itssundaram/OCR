"""A ruled table whose header (one or two printed lines) shares a rule row with the first data row, on a scan that is
a degree or two off, must still split header from data (MMS Collection RV)."""
from types import SimpleNamespace

from app.fast.tables import _split_merged_header


def _w(text, cx, cy, slope=0.0, mid=0.0, w=60, h=28):
    cy = cy + slope * (cx - mid)                      # a tilted scan: the right-hand side sits lower by slope*dx
    return SimpleNamespace(text=text, bbox=SimpleNamespace(x1=cx - w / 2, x2=cx + w / 2, y1=cy - h / 2, y2=cy + h / 2))


COLS = [0, 200, 400, 600, 800]
MID = 400


def _page(slope):
    head1 = [_w(t, x, 20, slope, MID) for t, x in [("Cos", 100), ("Part", 300), ("Batch", 500), ("Serial", 700)]]
    head2 = [_w(t, x, 60, slope, MID) for t, x in [("Section", 100), ("Number", 300), ("No", 500), ("No", 700)]]
    data = [_w(t, x, 100, slope, MID) for t, x in [("B1", 100), ("10528403", 300), ("S0047", 500), ("1779438", 700)]]
    return head1 + head2 + data


def test_two_line_header_is_split_from_data_even_on_a_tilted_scan():
    row_b = [0.0, 300.0]                                       # one rule row holding header and first data row
    for slope in (0.0, 0.02):
        got = _split_merged_header(row_b + [400.0], COLS, _page(slope), 0.0, 0.0, 28.0, slope, MID)
        assert len(got) == 4, slope                            # a cut was added
        assert 70 < got[1] < 90, (slope, got)                  # between the 2nd header line (y60) and the data (y100)


def test_a_cell_that_wraps_inside_one_column_is_not_split():
    words = [_w("Nomenclature", 300, 20), _w("Rifle", 300, 60), _w("Sight", 300, 90)]
    got = _split_merged_header([0.0, 300.0, 400.0], COLS, words, 0.0, 0.0, 28.0)
    assert len(got) == 3
