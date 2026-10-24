"""Department-ready JSON: stable keys, edits included, tables as keyed rows."""
from app.fast.tableschema import refresh_records, recount_quality
from app.services.export_json import build_export, column_keys
from app.services.table_edit import apply_cell_edit


def _table():
    headers = ["S No", "Pers No", "Rank", "Name", "Unit", "Qty", "Qty"]
    rows = [["1", "JC-12345A", "Sep", "Ram Singh", "9 ASSAM", "2", "3"], ["2", "JC-12346B", "Nk", "Shyam", "9 ASSAM", "4", ""]]
    cells = [{"row": 0, "col": c, "text": h, "is_header": True} for c, h in enumerate(headers)]
    cells += [{"row": r + 1, "col": c, "text": v, "is_header": False, "confidence": 0.9, "state": "accepted"}
              for r, row in enumerate(rows) for c, v in enumerate(row)]
    return {"table_name": "Table_1", "table_index": 1, "page_number": 1, "headers": headers, "rows": rows,
            "cells": cells, "meta": {"quality": {"columns": [], "rows": 2}}}


def test_column_keys_are_canonical_unique_and_never_empty():
    keys = [c["key"] for c in column_keys(["S No", "Pers No", "", "Remarks", "Remarks", "Col 7"])]
    assert keys[0] == "sno" and keys[1] == "pers_no"
    assert keys[2] == "column_3"
    assert keys[3] == "remarks" and keys[4] == "remarks_2"
    assert len(set(keys)) == len(keys)


def test_fields_split_into_template_and_additional():
    out = build_export({"department": "TMS", "doc_type": "backload", "template_version": 2, "pages_processed": 1, "fields": {
        "unit_from": {"value": "2 KUMAON", "confidence": 0.9},
        "stn": {"value": "c/o 56 APO", "source": "discovered", "label": "Stn"}}}, job_id="J1")
    assert out["fields"] == {"unit_from": "2 KUMAON"}
    assert out["additional_fields"] == {"stn": "c/o 56 APO"}
    assert out["template"] == "backload" and out["job_id"] == "J1" and out["document"]["pages"] == 1


def test_table_rows_are_keyed_and_complete():
    out = build_export({"tables": [_table()]})
    t = out["tables"][0]
    assert t["key"] == "table_1" and t["row_count"] == 2
    assert [c["key"] for c in t["columns"]][:2] == ["sno", "pers_no"]
    assert t["rows"][0]["pers_no"] == "JC-12345A" and t["rows"][0]["name"] == "Ram Singh"
    assert len(t["rows"][1]) == 7                       # every column present, missing cell is ""
    assert t["rows"][1]["qty_2"] == ""


def test_an_edited_cell_and_field_are_in_the_export_and_records_follow():
    table = _table()
    edited = apply_cell_edit(table, 1, 3, "Ram Singh Rawat")
    refresh_records(edited)
    ej = {"fields": {"iv_no": {"value": "20/4"}}, "tables": [edited]}
    out = build_export(ej)
    assert out["tables"][0]["rows"][0]["name"] == "Ram Singh Rawat"
    recs = out["tables"][0].get("records") or []
    assert recs and recs[0]["name"] == "Ram Singh Rawat"
    ej["fields"]["iv_no"]["value"] = "20/4/MT/02"        # what update-field stores
    assert build_export(ej)["fields"]["iv_no"] == "20/4/MT/02"


def test_stored_json_text_and_empty_inputs_do_not_crash():
    assert build_export('{"fields": {"a": {"value": "1"}}}')["fields"] == {"a": "1"}
    assert build_export(None)["fields"] == {} and build_export("not json")["tables"] == []


def test_duplicate_table_names_get_distinct_keys():
    a, b = _table(), _table()
    out = build_export({"tables": [a, b]})
    assert [t["key"] for t in out["tables"]] == ["table_1", "table_1_2"]
