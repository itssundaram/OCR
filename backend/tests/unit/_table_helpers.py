"""Synthetic raw tables (what app.fast.tables produces) for schema / refine tests."""
from types import SimpleNamespace

from app.pipelines.base import TableResult

EDGES = [100, 200, 500, 700, 900]


def raw_table(page: int, rows: list[list[str]], first_index: int = 1, conf: float = 0.9) -> TableResult:
    cells = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cells.append({"row": r, "col": c, "text": text, "page": page, "is_header": r == 0, "confidence": conf,
                          "bbox": {"x1": EDGES[c], "y1": 100 + r * 40, "x2": EDGES[c + 1], "y2": 140 + r * 40}})
    return TableResult(table_index=first_index, page_number=page, headers=rows[0], rows=rows[1:], cells=cells,
                       bbox={"x1": 100, "y1": 100, "x2": 900, "y2": 900}, extraction_method="grid_lines",
                       meta={"col_edges": EDGES})


def pages(*nums: int) -> dict:
    return {n: SimpleNamespace(width=1000, height=1400, text="") for n in nums}


NAMES = ["ASHA RAO", "BIJU NAIR", "CHETAN DAS", "DEEPA IYER", "ESHAN ROY", "FARAH KHAN", "GIRISH PAL", "HINA SEN",
         "IRFAN ALI", "JAYA MOHAN"]
SERV = ["ARMY", "NAVY", "ARMY", "AIRFORCE", "ARMY", "ARMY", "NAVY", "ARMY", "ARMY", "AIRFORCE"]
DATES = ["12-10-2003", "01-02-2002", "15-10-2003", "27-11-2002", "10-08-2003", "09-03-2004", "02-09-2004",
         "05-12-2001", "11-01-2003", "24-03-2003"]


def body(first: int, last: int) -> list[list[str]]:
    return [[str(i), NAMES[i - 1], DATES[i - 1], SERV[i - 1]] for i in range(first, last + 1)]


HEADER = ["S No", "Name", "DoB", "Service"]


def two_page_tables() -> tuple[list[TableResult], dict]:
    p1 = [["LIST OF CADETS 2026", "", "", ""], HEADER] + body(1, 6)
    p2 = [["LIST OF CADETS 2026", "", "", ""], HEADER] + body(7, 10) + [["TOTAL", "TEN (10)", "Cadets", ""]]
    return [raw_table(1, p1, 1), raw_table(2, p2, 2)], pages(1, 2)
