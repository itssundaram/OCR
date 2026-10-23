"""
OCR text recall measured against the PDF's own text layer (free ground truth, no hand labelling).

    python scripts/ocr_text_eval.py --docs-root "$HOME/mnt/OCR_DOCS" --family PSG --pages-per-doc 1 --rotate 270

Takes pages that HAVE a native text layer, renders them like a scan, optionally turns them (--rotate 90/270 or
--skew DEG, to imitate a sideways or tilted scan), reads them through the same PageReader path as a real job, and
reports how many of the native words (3+ letters/digits) the OCR recovered, per family. Use it to check any change to
page preparation (orientation, straightening, flattening) on pages whose true text is known.
Words in legacy Hindi fonts are excluded from the truth. Not a speed benchmark.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("TESSERACT_CMD", "/usr/bin/tesseract")

import pymupdf as fitz  # noqa: E402
from PIL import Image  # noqa: E402


def tokens(texts) -> Counter:
    c: Counter = Counter()
    for t in texts:
        for w in re.findall(r"[A-Za-z0-9]{3,}", t.lower()):
            c[w] += 1
    return c


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs-root", required=True)
    ap.add_argument("--family", default=None, help="only this first-level folder (PSG, TMS FORMAT, ...)")
    ap.add_argument("--pages-per-doc", type=int, default=1)
    ap.add_argument("--max-pages", type=int, default=12)
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="turn the rendered page clockwise by this much")
    ap.add_argument("--skew", type=float, default=0.0, help="then tilt it by this many degrees")
    ap.add_argument("--no-orientation", action="store_true", help="switch the sideways-page check off")
    ap.add_argument("--budget-s", type=float, default=130.0)
    args = ap.parse_args()

    from app.core.config import settings
    from app.fast.ocr_cache import OcrCache
    from app.fast.reader import PageReader
    from app.fast.triage import drop_legacy_words, legacy_font_rects
    from app.core.model_registry import get_tesseract_engine
    settings.FAST_ORIENTATION_ENABLED = not args.no_orientation
    eng = get_tesseract_engine()
    eng.load_model()
    reader = PageReader(None, OcrCache(tempfile.mkdtemp(prefix="ocrtxt_"), False), eng)

    t0 = time.time()
    per = defaultdict(lambda: [0, 0, 0])          # family -> pages, truth words, recovered words
    done = 0
    for path in sorted(Path(args.docs_root).rglob("*.pdf")):
        fam = path.relative_to(args.docs_root).parts[0]
        if args.family and fam != args.family:
            continue
        if time.time() - t0 > args.budget_s or done >= args.max_pages:
            break
        doc = fitz.open(path)
        used = 0
        for i, pg in enumerate(doc):
            if used >= args.pages_per_doc or done >= args.max_pages or time.time() - t0 > args.budget_s:
                break
            words = pg.get_text("words")
            if len(words) < 40:
                continue
            words = drop_legacy_words(words, legacy_font_rects(pg))
            truth = tokens(w[4] for w in words)
            if sum(truth.values()) < 30:
                continue
            pix = pg.get_pixmap(dpi=216)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if args.rotate:
                img = img.transpose({90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}[args.rotate])
            if args.skew:
                img = img.rotate(args.skew, resample=Image.BICUBIC, fillcolor=(255, 255, 255), expand=False)
            lines = reader._ocr_clean(img, i)
            got = tokens(l.text for l in lines)
            hit = sum(min(n, got.get(w, 0)) for w, n in truth.items())
            f = per[fam]
            f[0] += 1
            f[1] += sum(truth.values())
            f[2] += hit
            print(f"{fam:10} {path.name[:34]:34} p{i + 1:<3} recall {hit / sum(truth.values()):.0%}")
            used += 1
            done += 1
    print()
    for fam, (n, t, h) in sorted(per.items()):
        print(f"{fam:12} pages={n:<3} native words={t:<5} recovered={h / max(t, 1):.0%}  (rotate={args.rotate} skew={args.skew} orientation={'off' if args.no_orientation else 'on'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
