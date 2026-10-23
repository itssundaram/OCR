"""
Measure the fast_ocr read path on real files, stage by stage. No Redis, DB or Ollama needed.

    python scripts/bench_fast.py --docs "<folder or pdf>" [--pages 3] [--cold] [--gap-fill on|off] [--json out.json]

--cold uses a fresh empty OCR cache, so the numbers are what a NEW document costs; without it a second run
shows the warm (cached) cost. Reports per document: pages read, seconds for page reading (OCR), field
matching, tables, and the table / header counts, then a summary. Numbers depend on the machine; compare runs
on the same machine (before/after a change), not across machines.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("FAST_VLM_FALLBACK_ENABLED", "false")      # measure the deterministic path; VLM time is separate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", required=True)
    ap.add_argument("--pages", type=int, default=3, help="max pages read per document")
    ap.add_argument("--cold", action="store_true", help="empty OCR cache")
    ap.add_argument("--gap-fill", choices=["on", "off", "default"], default="default", help="whole-page gap-fill read (default = the setting)")
    ap.add_argument("--limit", type=int, default=0, help="max documents (0 = all)")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    if a.gap_fill != "default":
        os.environ["FAST_GAP_FILL_ENABLED"] = "true" if a.gap_fill == "on" else "false"

    from app.core.config import settings
    from app.fast.engine import FastEngine
    from app.fast.events import EventBus
    from app.fast.ocr_cache import OcrCache
    from app.fast.reader import PageReader
    from app.fast.triage import DocumentSource

    files = [a.docs] if a.docs.lower().endswith(".pdf") else sorted(glob.glob(os.path.join(a.docs, "**", "*.pdf"), recursive=True))
    files = [f for f in files if "/_" not in f.replace("\\", "/")]
    if a.limit:
        files = files[: a.limit]
    cache_dir = tempfile.mkdtemp(prefix="bench_cache_") if a.cold else os.path.join(tempfile.gettempdir(), "bench_cache_warm")
    cache = OcrCache(cache_dir, True)
    rows = []
    for f in files:
        t0 = time.perf_counter()
        try:
            src = DocumentSource(f, "application/pdf")
            n = min(src.page_count, a.pages)
            bus = EventBus("bench")
            bus._redis = None
            eng = FastEngine([], bus, want_tables=True)
            eng.run_pages(n, PageReader(src, cache).read)
            t_read = time.perf_counter()
            eng.finalize_fields()
            tabs = eng.collect_tables()
            t_end = time.perf_counter()
            ocr_pages = sum(1 for p in eng.sorted_pages() if p.source == "ocr")
            rows.append({"file": os.path.basename(f), "pages": n, "ocr_pages": ocr_pages,
                         "read_s": round(t_read - t0, 2), "fields_tables_s": round(t_end - t_read, 2),
                         "total_s": round(t_end - t0, 2), "tables": len(tabs),
                         "s_per_ocr_page": round((t_read - t0) / ocr_pages, 2) if ocr_pages else None})
        except Exception as exc:
            rows.append({"file": os.path.basename(f), "error": repr(exc)[:160]})
        print(rows[-1], flush=True)
    ok = [r for r in rows if "error" not in r]
    print(f"\n{len(ok)}/{len(rows)} documents | OCR cache: {'cold' if a.cold else 'warm'} | gap-fill: {settings.FAST_GAP_FILL_ENABLED} | "
          f"page threads: {settings.FAST_PAGE_THREADS} | OMP threads/tesseract: {settings.TESSERACT_OMP_THREADS}")
    scanned = [r for r in ok if r["s_per_ocr_page"]]
    if scanned:
        v = sorted(r["s_per_ocr_page"] for r in scanned)
        print(f"seconds per scanned page: median {v[len(v)//2]}  min {v[0]}  max {v[-1]}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2)
    if a.cold:
        shutil.rmtree(cache_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
