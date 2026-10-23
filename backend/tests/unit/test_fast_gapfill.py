import numpy as np
from PIL import Image, ImageDraw

from app.fast.gapfill import find_gap_regions, fill_gaps
from app.ocr.base import BoundingBox, OCRLine, OCRWord


def _page():
    im = Image.new("RGB", (1200, 1600), "white")
    d = ImageDraw.Draw(im)
    for i in range(6):                                   # a "word" of dark blocks (stands in for printed text)
        d.rectangle([300 + i * 22, 400, 300 + i * 22 + 12, 428], fill=(10, 10, 10))
    return im


def test_uncovered_dark_text_is_found_and_covered_text_is_not():
    im = _page()
    assert find_gap_regions(im, []), "uncovered ink must be reported"
    covered = OCRLine(text="x", words=[OCRWord(text="x", bbox=BoundingBox(x1=290, y1=390, x2=450, y2=440), confidence=0.9)],
                      bbox=BoundingBox(x1=290, y1=390, x2=450, y2=440), confidence=0.9)
    assert find_gap_regions(im, [covered]) == []


def test_faint_watermark_dashes_and_rules_are_ignored():
    im = Image.new("RGB", (1200, 1600), "white")
    d = ImageDraw.Draw(im)
    for i in range(6):
        d.rectangle([300 + i * 22, 400, 300 + i * 22 + 12, 428], fill=(200, 200, 200))     # faint
    for i in range(30):
        d.rectangle([100 + i * 20, 900, 100 + i * 20 + 10, 903], fill=(0, 0, 0))            # dashed separator
    d.rectangle([100, 1200, 1100, 1203], fill=(0, 0, 0))                                    # ruling line
    assert find_gap_regions(im, []) == []


class _Engine:
    def _ocr_image(self, crop, idx, psm=6):
        w, h = crop.size
        wd = OCRWord(text="WORD", bbox=BoundingBox(x1=12, y1=12, x2=w - 12, y2=h - 12), confidence=0.9)
        return [OCRLine(text="WORD", words=[wd], bbox=wd.bbox, confidence=0.9)]


def test_words_are_mapped_back_into_page_pixels_and_original_lines_are_kept():
    im = _page()
    new, stats = fill_gaps(im, [], _Engine(), budget_s=5)
    assert stats.words_added >= 1 and len(new) >= 1
    b = new[0].words[0].bbox
    assert 280 <= b.x1 <= 310 and 380 <= b.y1 <= 410 and 420 <= b.y2 <= 450      # lands on the original ink


def test_nothing_found_returns_the_same_lines():
    im = Image.new("RGB", (800, 800), "white")
    lines = []
    out, stats = fill_gaps(im, lines, _Engine())
    assert out is lines and stats.regions == 0
