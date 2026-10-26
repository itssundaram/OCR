"""
Department API smoke test: department x template x real file, through the real HTTP API.

    python scripts/api_smoke_test.py --docs C:\\sundaram\\OCR_DOCS                (fast_ocr, all sample files)
    python scripts/api_smoke_test.py --docs C:\\sundaram\\OCR_DOCS --base http://localhost:8000/api/v1 --pipeline fast_ocr
    python scripts/api_smoke_test.py --docs ... --only TMS --endpoints          (also exercise the other endpoints)

What it does
  1. makes sure each department + template below exists and is active (creates what is missing; never edits an
     existing template, so run it against your real DB without fear: it only ADDS the templates it needs, named
     smoke_* so they are easy to find and delete)
  2. uploads every sample file to POST /{department}/{template}/pipeline/{pipeline}
  3. polls GET /{department}/{template}/status/{job_id} until the job ends, reads fields, tables and export
  4. prints a table and writes api_smoke_results.json next to this script

It needs the API and the workers (python run_worker.py) to be running. Needs `requests`.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import requests

F = lambda name, typ="text", aliases=(): {"name": name, "type": typ, "description": name.replace("_", " "), "aliases": list(aliases)}  # noqa: E731

TEMPLATES = {
    ("TMS", "smoke_backload"): [F("unit_from", aliases=["Unit"]), F("unit_to", aliases=["Issued To"]), F("iv_no", aliases=["IV No"]), F("auth", aliases=["Auth"]), F("ba_no", aliases=["BA No", "Veh BA No"])],
    ("TMS", "smoke_engine_chasis"): [F("engine_no", aliases=["Engine No"]), F("chassis_no", aliases=["Chassis No", "Chasis No"]), F("ba_no", aliases=["BA No"])],
    ("TMS", "smoke_issue_voucher"): [F("from_depot", aliases=["From", "Depot"]), F("unit_to", aliases=["Consign To"]), F("date", "date", ["Date"]), F("iv_no", aliases=["Issue Voucher No"]), F("ba_no", aliases=["BA No"]), F("hull_no", aliases=["Hull No"]), F("engine_no", aliases=["Eng No"]), F("consignor", aliases=["Consignor"]), F("consignee", aliases=["Consignee"])],
    ("TMS", "smoke_transfer"): [F("unit_from"), F("unit_to"), F("date", "date", ["Date"]), F("iv_no", aliases=["IV No"]), F("auth", aliases=["Auth"]), F("ba_no", aliases=["BA No"])],
    ("MMS", "smoke_collection_rv"): [F("type_of_str", aliases=["Type of STR"]), F("from_depot", aliases=["From", "Depot"]), F("unit_to", aliases=["To", "Unit"]), F("rv_no", aliases=["RV No"]), F("date", "date", ["Date"]), F("sus_no", aliases=["SUS No"])],
    ("MMS", "smoke_deposit_voucher"): [F("unit_from"), F("unit_to"), F("date", "date", ["Date"]), F("iv_no", aliases=["IV No"]), F("auth", aliases=["Auth"])],
    ("PSG", "smoke_personnel_table"): [F("pers_no", aliases=["Pers No"]), F("name", aliases=["Name"]), F("rank", aliases=["Rank"]), F("dob", "date", ["DOB"]), F("doc", "date", ["DOC"]), F("dos", "date", ["DOS"])],
    ("CUE_PSG", "smoke_war_establishment"): [F("we_no", aliases=["WE No"]), F("effective_from", "date", ["Effective From"]), F("strength_total", aliases=["Total Strength"])],
}
TMS_DIR, MMS_DIR, PSG_DIR, CUE_DIR = "TMS FORMAT", "MMS", "PSG", "CUE"
FILES = [
    ("TMS", "smoke_backload", f"{TMS_DIR}/BACKLOAD1.pdf"), ("TMS", "smoke_backload", f"{TMS_DIR}/BACKLOAD2.pdf"),
    ("TMS", "smoke_engine_chasis", f"{TMS_DIR}/ENGINE CHASIS NO1.pdf"), ("TMS", "smoke_engine_chasis", f"{TMS_DIR}/ENGINE CHASIS NO2.pdf"),
    ("TMS", "smoke_issue_voucher", f"{TMS_DIR}/Issue to Unit A Veh.pdf"), ("TMS", "smoke_issue_voucher", f"{TMS_DIR}/Issue to Unit (2) A Veh.pdf"),
    ("TMS", "smoke_issue_voucher", f"{TMS_DIR}/Veh deposit to Depot A Veh.pdf"), ("TMS", "smoke_issue_voucher", f"{TMS_DIR}/ALLOCATION OF BA NO.pdf"),
    ("TMS", "smoke_transfer", f"{TMS_DIR}/TRANSFER1.pdf"), ("TMS", "smoke_transfer", f"{TMS_DIR}/TRANSFER2.pdf"),
    ("MMS", "smoke_collection_rv", f"{MMS_DIR}/Collection_RV.pdf"), ("MMS", "smoke_deposit_voucher", f"{MMS_DIR}/Deposit_RV.pdf"),
    ("MMS", "smoke_deposit_voucher", f"{MMS_DIR}/Hand Over_RV.pdf"),
] + [("PSG", "smoke_personnel_table", f"{PSG_DIR}/{n}") for n in [
    "ADC.pdf", "AMC 1.pdf", "AMC COMMN.pdf", "APS.pdf", "Commission 1.pdf", "Commission 2.pdf", "DENTAL_0001.pdf", "MNS.pdf",
    "RVC.pdf", "SSCW [TECH].pdf", "TA COMMISSION LETTER_0001.pdf", "TES.pdf", "TGC.pdf"]] + [
    ("CUE_PSG", "smoke_war_establishment", f"{CUE_DIR}/Revised WE Inf Bns Model 4B_0001.pdf"),
    ("CUE_PSG", "smoke_war_establishment", f"{CUE_DIR}/SD-6 Revised WE of Indep Sup Pl ASC_0001.pdf"),
]


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def __call__(self, method, path, **kw):
        return requests.request(method, self.base + path, timeout=120, **kw)


def ensure_templates(api, only):
    api("POST", "/departments/seed")
    have = {(t["department_id"], t["code"]): t for t in api("GET", "/templates").json().get("data", []) if t.get("is_active")}
    depts = {d["slug"]: d["id"] for d in api("GET", "/departments/all").json()["data"]}
    for (dept, code), fields in TEMPLATES.items():
        if only and dept != only:
            continue
        if (depts.get(dept), code) in have:
            continue
        r = api("POST", f"/templates/{dept}", json={"code": code, "name": code, "template_json": {"fields": fields}})
        if r.status_code == 201:
            api("POST", f"/templates/{r.json()['data']['id']}/activate")
            print(f"  created + activated template {dept}/{code}")
        else:
            print(f"  could not create {dept}/{code}: {r.status_code} {r.text[:120]}")


def run_files(api, pipeline, docs, only, timeout):
    out = []
    for dept, code, rel in FILES:
        if only and dept != only:
            continue
        p = docs / rel
        if not p.exists():
            out.append({"dept": dept, "template": code, "file": rel, "status": "MISSING_FILE"})
            print(f"{dept:8} {code:24} {p.name:40} file not found")
            continue
        t0 = time.time()
        with open(p, "rb") as fh:
            r = api("POST", f"/{dept.lower()}/{code}/pipeline/{pipeline}", files={"file": (p.name, fh, "application/pdf")})
        rec = {"dept": dept, "template": code, "file": rel, "http": r.status_code}
        if r.status_code != 202:
            rec.update(status="UPLOAD_REFUSED", error=r.text[:200]); out.append(rec)
            print(f"{dept:8} {code:24} {p.name:40} refused {r.status_code}")
            continue
        jid = r.json()["data"]["job_id"]; rec["job_id"] = jid
        s = {}
        while time.time() - t0 < timeout:
            s = api("GET", f"/{dept.lower()}/{code}/status/{jid}").json()["data"]
            if s["status"] in ("COMPLETED", "WARNING", "FAILED"):
                break
            time.sleep(2)
        rec["status"] = s.get("status"); rec["seconds"] = round(time.time() - t0, 1)
        res = s.get("result") or {}
        fields = res.get("fields") or {}
        rec["fields"] = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in fields.items()}
        rec["tables"] = [{"page": t.get("page_number"), "rows": len(t.get("rows") or []), "cols": len(t.get("headers") or [])} for t in (res.get("tables") or [])]
        exp = s.get("export") or {}
        rec["export_ok"] = bool(exp) and "fields" in exp
        rec["error"] = s.get("error_message")
        out.append(rec)
        template_fields = [k for k in rec["fields"] if k in {f["name"] for f in TEMPLATES[(dept, code)]}]
        filled = sum(1 for k in template_fields if rec["fields"][k])
        print(f"{dept:8} {code:24} {p.name:40} {rec['status']:9} {rec['seconds']:6}s  fields {filled}/{len(TEMPLATES[(dept, code)])}  tables {len(rec['tables'])}  export {'ok' if rec['export_ok'] else 'MISSING'}")
    return out


def endpoint_checks(api, job_id, dept, code):
    """GET/POST checks over the rest of the API using one finished job. Returns [(name, ok)]."""
    rows = []

    def t(name, method, path, expect=(200,), **kw):
        try:
            r = api(method, path, **kw)
            ok = r.status_code in expect
            rows.append((f"{method} {path}", r.status_code, ok))
            return r
        except Exception as exc:  # noqa: BLE001
            rows.append((f"{method} {path}", str(exc)[:60], False))

    for path in ("/health", "/pipelines", "/workers/health", "/system/profile", "/dashboard/stats", "/events/recent",
                 "/departments", "/departments/all", "/templates", "/department-urls", "/documents/", "/processing/"):
        t(path, "GET", path)
    t("status", "GET", f"/{dept.lower()}/{code}/status/{job_id}")
    t("endpoints", "GET", f"/{dept.lower()}/{code}/endpoints")
    t("export", "GET", f"/extract/jobs/{job_id}/export")
    t("job", "GET", f"/processing/{job_id}")
    t("steps", "GET", f"/processing/{job_id}/pipeline-steps")
    t("events", "GET", f"/processing/{job_id}/events")
    t("refine", "GET", f"/extract/jobs/{job_id}/refine")
    # error paths must be 4xx, never 500
    t("unknown pipeline", "POST", f"/{dept.lower()}/{code}/pipeline/nope", (404,), files={"file": ("a.pdf", b"%PDF-1.4 x", "application/pdf")})
    t("unknown dept", "POST", f"/nodept/x/pipeline/fast_ocr", (404,), files={"file": ("a.pdf", b"%PDF-1.4 x", "application/pdf")})
    t("unknown template", "POST", f"/{dept.lower()}/nosuch/pipeline/fast_ocr", (404,), files={"file": ("a.pdf", b"%PDF-1.4 x", "application/pdf")})
    t("unknown job", "GET", f"/{dept.lower()}/{code}/status/does-not-exist", (404,))
    t("not a pdf", "POST", f"/{dept.lower()}/{code}/pipeline/fast_ocr", (400,), files={"file": ("a.pdf", b"hello", "application/pdf")})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000/api/v1")
    ap.add_argument("--docs", required=True, help="folder holding the TMS FORMAT / MMS / PSG / CUE sample folders")
    ap.add_argument("--pipeline", default="fast_ocr")
    ap.add_argument("--only", help="one department slug, e.g. TMS")
    ap.add_argument("--timeout", type=int, default=420, help="seconds to wait per document")
    ap.add_argument("--endpoints", action="store_true", help="also run the other-endpoint checks on the last finished job")
    a = ap.parse_args()
    api = Api(a.base)
    try:
        api("GET", "/health").raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"API not reachable at {a.base}: {exc}")
        return 2
    ensure_templates(api, a.only)
    results = run_files(api, a.pipeline, pathlib.Path(a.docs), a.only, a.timeout)
    failed = [r for r in results if r.get("status") not in ("COMPLETED", "WARNING")]
    if a.endpoints:
        last = next((r for r in reversed(results) if r.get("job_id") and r.get("status") in ("COMPLETED", "WARNING")), None)
        if last:
            rows = endpoint_checks(api, last["job_id"], last["dept"], last["template"])
            for name, code, ok in rows:
                print(("PASS" if ok else "FAIL"), code, name)
            failed += [r for r in rows if not r[2]]
    out = pathlib.Path(__file__).with_name("api_smoke_results.json")
    out.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(f"\n{len(results)} documents, {len(failed)} failed. Details: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
