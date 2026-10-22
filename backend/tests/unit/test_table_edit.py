import pytest
from app.services.table_edit import apply_cell_edit, cells_from_grid, rebuild_from_cells


def _table():
    return {
        "headers": ["No", "Name"],
        "rows": [["1", "Capt A"], ["2", "Capt B"]],
        "cells": cells_from_grid(["No", "Name"], [["1", "Capt A"], ["2", "Capt B"]]),
        "markdown": "",
    }


def test_edit_updates_cell_rows_and_markdown():
    t = apply_cell_edit(_table(), row=2, col=1, value="Capt Bikash | Das")
    assert t["rows"][1] == ["2", "Capt Bikash | Das"]
    assert "Capt Bikash \\| Das" in t["markdown"]  # pipe escaped so the grid cannot break
    edited = [c for c in t["cells"] if c["row"] == 2 and c["col"] == 1][0]
    assert edited["edited"] is True and edited["confidence"] == 1.0


def test_editing_header_row_changes_headers():
    assert apply_cell_edit(_table(), 0, 0, "S.No")["headers"] == ["S.No", "Name"]


def test_original_table_is_not_mutated():
    t = _table()
    apply_cell_edit(t, 1, 1, "X")
    assert t["rows"][0] == ["1", "Capt A"]


def test_table_without_cells_can_still_be_edited():
    t = {"headers": ["a", "b"], "rows": [["1", "2"]], "markdown": ""}
    out = apply_cell_edit(t, 1, 0, "9")
    assert out["rows"] == [["9", "2"]]
    assert len(out["cells"]) == 4


def test_missing_cell_in_grid_is_created():
    t = _table()
    t["cells"] = [c for c in t["cells"] if not (c["row"] == 1 and c["col"] == 1)]
    out = apply_cell_edit(t, 1, 1, "filled")
    assert out["rows"][0] == ["1", "filled"]


@pytest.mark.parametrize("row,col", [(-1, 0), (0, -1), (3, 0), (0, 2)])
def test_out_of_range_raises(row, col):
    with pytest.raises(ValueError):
        apply_cell_edit(_table(), row, col, "x")


def test_rebuild_is_noop_without_cells():
    t = {"headers": ["a"], "rows": [["1"]]}
    assert rebuild_from_cells(t) == t
