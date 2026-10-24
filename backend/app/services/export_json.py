"""
Department-ready JSON.

The stored result (ProcessingResult.extracted_json) carries everything the review screen needs: per-field
confidence, boxes, checks, per-cell state. A department system that receives the data over an API wants none of
that: it wants stable keys to bind to. `build_export` turns the stored result into that, and is computed every
time it is asked for, so a corrected field or table cell is always in it.

    {
      "schema_version": "1.0",
      "job_id": "...", "department": "TMS", "template": "backload", "template_version": 3,
      "document": {"pages": 2},
      "fields":            {"unit_from": "2 KUMAON", "iv_no": "20/4/MT/02", ...},     the template's fields
      "additional_fields": {"stn": "c/o 56 APO", ...},                                   found on the document, not in the template
      "tables": [{
          "key": "table_1", "name": "Table_1", "page": 1, "row_count": 3,
          "columns": [{"index": 0, "key": "sno", "label": "S No"}, ...],
          "rows":    [{"sno": "1", "pers_no": "JC-12345A", ...}, ...],               one object per row, every column, keyed
          "records": [...]                                                           only when the columns are recognised (fused columns split, dates dd-mm-yyyy)
      }]
    }

Column keys are the canonical column id when the header is a known one ("sno", "pers_no", "sap_number" ...), else
the header text in snake_case, else "column_N"; duplicates get _2, _3. They do not change when a cell is edited,
only when the header text itself is.
"""
from __future__ import annotations

import json
import re
from typing import Any

SCHEMA_VERSION = "1.0"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")


def column_keys(headers: list[Any]) -> list[dict]:
    """[{index, key, label}] — one stable, unique key per column."""
    heads = [str(h or "").strip() for h in headers or []]
    try:
        from app.knowledge.kb import normalize_headers
        known = normalize_headers(heads)
    except Exception:
        known = [{"id": "", "text": h} for h in heads]
    out: list[dict] = []
    used: set[str] = set()
    for i, (h, k) in enumerate(zip(heads, known)):
        base = (k.get("id") or "") or _slug(h) or f"column_{i + 1}"
        if re.fullmatch(r"column_?\d+", base):
            base = f"column_{i + 1}"
        key, n = base, 1
        while key in used:
            n += 1
            key = f"{base}_{n}"
        used.add(key)
        out.append({"index": i, "key": key, "label": h or f"Column {i + 1}"})
    return out


def export_table(table: dict, position: int = 0) -> dict:
    cols = column_keys(table.get("headers") or [])
    rows_out = []
    for row in table.get("rows") or []:
        cells = list(row or [])
        rows_out.append({c["key"]: ("" if c["index"] >= len(cells) or cells[c["index"]] is None else str(cells[c["index"]]).strip())
                         for c in cols})
    name = str(table.get("table_name") or f"Table_{table.get('table_index', position + 1)}")
    out = {
        "key": _slug(name) or f"table_{position + 1}",
        "name": name,
        "page": table.get("page_number"),
        "row_count": len(rows_out),
        "columns": cols,
        "rows": rows_out,
    }
    try:                                           # rebuilt from the current rows, never trusted from storage (a cell may have been corrected since)
        from app.knowledge.kb import normalize_headers, table_records
        records = table_records(normalize_headers([c["label"] if c["label"] != f"Column {c['index'] + 1}" else "" for c in cols]),
                                table.get("rows") or [])
    except Exception:
        records = (table.get("meta") or {}).get("records")
    if isinstance(records, list) and records:
        out["records"] = records
    return out


def build_export(extracted_json: dict | None, *, job_id: str | None = None, detail: bool = False) -> dict:
    ej = extracted_json
    while isinstance(ej, str):                                     # some pipelines store the JSON as text
        try:
            ej = json.loads(ej)
        except ValueError:
            ej = {}
    ej = ej if isinstance(ej, dict) else {}
    fields: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    details: dict[str, Any] = {}
    for key, d in (ej.get("fields") or {}).items():
        d = d if isinstance(d, dict) else {"value": d}
        value = "" if d.get("value") is None else d.get("value")
        (extra if d.get("source") == "discovered" else fields)[key] = value
        if detail:
            details[key] = {
                "label": d.get("label") or "", "confidence": d.get("confidence"), "page": d.get("page_number") or d.get("source_page"),
                "verified": bool(d.get("is_verified") or d.get("manually_edited")), "state": d.get("state") or "",
            }
    tables = [export_table(t, i) for i, t in enumerate(ej.get("tables") or []) if isinstance(t, dict)]
    seen: dict[str, int] = {}
    for t in tables:                                               # two tables with the same name keep distinct keys
        n = seen.get(t["key"], 0) + 1
        seen[t["key"]] = n
        if n > 1:
            t["key"] = f"{t['key']}_{n}"
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        **({"job_id": job_id} if job_id else {}),
        "department": ej.get("department"),
        "template": ej.get("doc_type"),
        "template_version": ej.get("template_version"),
        "document": {"pages": ej.get("pages_processed")},
        "fields": fields,
        "additional_fields": extra,
        "tables": tables,
    }
    if detail:
        out["field_details"] = details
    return out
