"""
DOCINT — Table cell editing (pure helpers)
==========================================
The Line-items grid lets a reviewer correct a cell. These helpers apply that
edit to a stored table dict (the shape app/services/orchestrator.py writes
into ProcessingResult.extracted_json["tables"]) and keep every derived view of
the table — headers, rows and markdown — consistent with the edited cells.

No DB, no FastAPI: the route in app/api/routes/extract.py only loads the
table, calls apply_cell_edit(), and saves the result.
"""
from __future__ import annotations

from copy import deepcopy

MAX_GRID_SIDE = 500  # sanity bound; real tables are far smaller


def cells_from_grid(headers: list, rows: list[list]) -> list[dict]:
    """Build cell dicts for a table that only has headers/rows (for example
    one read by the img2table fallback, which has no per-cell geometry)."""
    cells: list[dict] = []
    grid = [list(headers or [])] + [list(r) for r in (rows or [])]
    for r_idx, row in enumerate(grid):
        for c_idx, text in enumerate(row):
            cells.append({
                "row": r_idx,
                "col": c_idx,
                "text": "" if text is None else str(text),
                "bbox": None,
                "is_header": r_idx == 0,
                "confidence": None,  # no per-cell score from this reader
            })
    return cells


def _grid(cells: list[dict]) -> list[list[str]]:
    n_rows = max(c["row"] for c in cells) + 1
    n_cols = max(c["col"] for c in cells) + 1
    grid = [[""] * n_cols for _ in range(n_rows)]
    for c in cells:
        grid[c["row"]][c["col"]] = "" if c.get("text") is None else str(c["text"])
    return grid


def _markdown(headers: list[str], body: list[list[str]]) -> str:
    def esc(v: str) -> str:
        return str(v).replace("|", "\\|").replace("\n", " ")

    sep = " | ".join(["---"] * len(headers))
    md = f"| {' | '.join(esc(h) for h in headers)} |\n| {sep} |\n"
    for row in body:
        md += f"| {' | '.join(esc(v) for v in row)} |\n"
    return md


def rebuild_from_cells(table: dict) -> dict:
    """Recompute headers / rows / markdown from table['cells'] (row 0 = header)."""
    cells = table.get("cells") or []
    if not cells:
        return table
    grid = _grid(cells)
    table["headers"] = grid[0]
    table["rows"] = grid[1:]
    table["markdown"] = _markdown(grid[0], grid[1:])
    return table


def apply_cell_edit(table: dict, row: int, col: int, value: str) -> dict:
    """Return a copy of `table` with cell (row, col) set to `value`.

    The edited cell is marked edited with confidence 1.0 (a human read it).
    Raises ValueError if (row, col) is outside the table's grid."""
    if row < 0 or col < 0 or row > MAX_GRID_SIDE or col > MAX_GRID_SIDE:
        raise ValueError("Cell position is out of range.")

    out = deepcopy(table)
    cells = out.get("cells") or []
    if not cells:
        cells = cells_from_grid(out.get("headers") or [], out.get("rows") or [])
    if not cells:
        raise ValueError("This table has no cells to edit.")

    n_rows = max(c["row"] for c in cells) + 1
    n_cols = max(c["col"] for c in cells) + 1
    if row >= n_rows or col >= n_cols:
        raise ValueError(f"Cell ({row}, {col}) is outside the {n_rows}x{n_cols} table.")

    target = next((c for c in cells if c["row"] == row and c["col"] == col), None)
    if target is None:  # the grid has a hole here (empty cell the reader skipped)
        target = {"row": row, "col": col, "text": "", "bbox": None, "is_header": row == 0}
        cells.append(target)
    if "raw_text" not in target and target.get("text") != value:
        target["raw_text"] = target.get("text", "")
    target["text"] = value
    target["confidence"] = 1.0
    target["edited"] = True
    target["state"] = "accepted"          # a person read it: no flag, no suggestion
    target["reasons"] = []
    target["rev"] = int(target.get("rev", 0)) + 1
    target.pop("suggestion", None)

    out["cells"] = cells
    out = rebuild_from_cells(out)
    try:                                   # keep the table summary (accepted / flagged) current
        from app.fast.tableschema import recount_quality
        recount_quality(out)
    except Exception:
        pass
    return out
