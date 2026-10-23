"""
Field accuracy against hand-checked values (a "golden set"), page by page.

    python scripts/accuracy_eval.py tests/golden/tms_backload2.json --docs-root "$HOME/mnt/OCR_DOCS"
    python scripts/accuracy_eval.py tests/golden/*.json --docs-root ... --legacy-prep    # without the new page preparation

Runs the same engine as the fast_ocr pipeline (OCR + field extraction, VLM repair OFF unless --vlm), then for
every golden entry (field, page, expected value) takes the reading the job produced for THAT page: the field's
main reading when it sits on that page, else its other-page occurrence. Prints per-field results, the totals
and a calibration table (how often a reading at a given confidence was right), and optionally writes JSON.

Golden file: {"doc": "<path under docs-root>", "fields": [{"name", "page", "value"}, ...]}
A reading counts as correct when its text matches the expected one at >= 0.9 similarity after case/punctuation
folding. Nothing here is a benchmark of speed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def similarity(a: str, b: str) -> float:
    a, b = fold(a), fold(b)
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def reading_on_page(field_result, page: int) -> tuple[str, float, str] | None:
    if field_result is None:
        return None
    if field_result.page_number == page and field_result.value:
        return field_result.value, field_result.confidence, field_result.extraction_method
    for o in field_result.occurrences or []:
        if o.get("page_number") == page:
            return o["value"], float(o.get("confidence") or 0.0), o.get("source", "")
    return None


def run_doc(path: str, field_names: list[str], cache_dir: str, use_vlm: bool):
    os.environ.setdefault("FAST_VLM_FALLBACK_ENABLED", "true" if use_vlm else "false")
    from app.fast.engine import FastEngine
    from app.fast.events import EventBus
    from app.fast.ocr_cache import OcrCache
    from app.fast.reader import PageReader
    from app.fast.triage import DocumentSource

    src = DocumentSource(path, "application/pdf")
    bus = EventBus("eval")
    bus._redis = None
    eng = FastEngine([{"name": n} for n in field_names], bus, want_tables=False)
    reader = PageReader(src, OcrCache(cache_dir, True))
    eng.run_pages(src.page_count, reader.read)
    return {f.field_name: f for f in eng.finalize_fields()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("golden", nargs="+")
    ap.add_argument("--docs-root", required=True)
    ap.add_argument("--cache-dir", default=None, help="OCR cache (default: a fresh temp dir, so every run reads the pages)")
    ap.add_argument("--legacy-prep", action="store_true", help="switch off text-based straightening and lighting flattening")
    ap.add_argument("--vlm", action="store_true", help="let the vision model repair doubtful fields (needs Ollama)")
    ap.add_argument("--json", default=None, help="write the full report here")
    args = ap.parse_args()

    os.environ.setdefault("TESSERACT_CMD", "/usr/bin/tesseract")
    from app.fast import linefree
    if args.legacy_prep:
        linefree.TEXT_SKEW_MIN_GAIN, linefree.FLATTEN_BELOW = 999.0, 0

    cache_dir = args.cache_dir or tempfile.mkdtemp(prefix="eval_ocr_")
    rows, buckets = [], defaultdict(lambda: [0, 0])
    for gpath in args.golden:
        g = json.loads(Path(gpath).read_text(encoding="utf-8"))
        names = sorted({e["name"] for e in g["fields"]})
        results = run_doc(os.path.join(args.docs_root, g["doc"]), names, cache_dir, args.vlm)
        for e in g["fields"]:
            got = reading_on_page(results.get(e["name"]), e["page"])
            value, conf, how = got if got else ("", 0.0, "not_found")
            sim = similarity(value, e["value"])
            ok = sim >= 0.9
            rows.append({"doc": g["doc"], "field": e["name"], "page": e["page"], "expected": e["value"],
                         "got": value, "similarity": round(sim, 3), "correct": ok, "confidence": round(conf, 3), "how": how})
            if got:
                b = buckets[min(int(conf * 5), 4)]
                b[0] += 1
                b[1] += int(ok)

    n = len(rows)
    found = sum(1 for r in rows if r["got"])
    correct = sum(1 for r in rows if r["correct"])
    print(f"{'field':14} {'pg':>2} {'sim':>5} {'conf':>5}  expected -> got")
    for r in rows:
        mark = "OK " if r["correct"] else ("~  " if r["similarity"] >= 0.6 else "BAD" if r["got"] else "---")
        print(f"{r['field']:14} {r['page']:>2} {r['similarity']:>5.2f} {r['confidence']:>5.2f}  {mark} {r['expected'][:40]!r} -> {r['got'][:40]!r}")
    mean_sim = sum(r["similarity"] for r in rows) / max(1, n)
    print(f"\nreadings {n}: found {found} ({found / max(1, n):.0%}), correct {correct} ({correct / max(1, n):.0%}), mean text similarity {mean_sim:.2f}"
          f"{'  [legacy page preparation]' if args.legacy_prep else ''}")
    print("calibration (confidence band -> share correct):")
    for i in range(5):
        tot, ok = buckets[i]
        if tot:
            print(f"  {i * 20:>3}-{i * 20 + 20:<3}%  n={tot:<3} correct={ok / tot:.0%}")
    if args.json:
        Path(args.json).write_text(json.dumps({"rows": rows, "found": found, "correct": correct, "n": n}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
