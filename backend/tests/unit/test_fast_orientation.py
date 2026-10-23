"""Sideways-page handling: projection test, turn mapping, and the keep-only-if-better rule in PageReader."""
import cv2
import numpy as np
from PIL import Image, ImageDraw

from app.fast.orientation import (Orientation, accept_rotation, good_words, rotate_image, sideways_ratio,
                                  unrotate_xy)
from app.fast.reader import PageReader, _unrotate_box
from app.ocr.base import BoundingBox, OCRLine, OCRWord


def _text_page(width=900, height=1200):
    img = Image.new("L", (width, height), 255)
    d = ImageDraw.Draw(img)
    y = 60
    while y < height - 60:
        x = 60
        while x < width - 120:
            w = 30 + (x * 7 + y) % 50
            d.rectangle([x, y, x + w, y + 14], fill=0)       # a "word": ink block the height of a text line
            x += w + 18
        y += 34
    return img


def test_sideways_ratio_separates_upright_from_turned():
    page = _text_page()
    assert sideways_ratio(np.array(page)) > 0.7
    assert sideways_ratio(np.array(page.transpose(Image.ROTATE_90))) < 0.7


def test_rotate_and_unrotate_round_trip():
    w, h = 400, 300
    for rot in (0, 90, 180, 270):
        im = Image.new("L", (w, h), 255)
        im.putpixel((100, 40), 0)
        turned = rotate_image(im, rot)
        assert turned.size == ((h, w) if rot in (90, 270) else (w, h))
        xs, ys = np.where(np.array(turned) == 0)
        x, y = unrotate_xy(float(ys[0]), float(xs[0]), rot, w, h)
        assert abs(x - 100) <= 1.5 and abs(y - 40) <= 1.5


def test_unrotate_box_stays_ordered_and_inside_the_page():
    b = _unrotate_box(BoundingBox(x1=10, y1=20, x2=70, y2=40), 270, 500, 800)
    assert b.x1 < b.x2 and b.y1 < b.y2
    assert 0 <= b.x1 and b.x2 <= 500 and 0 <= b.y1 and b.y2 <= 800


def test_accept_rotation_needs_clear_gain():
    assert accept_rotation(10, 100)
    assert not accept_rotation(100, 120)      # 20 % better is not enough
    assert not accept_rotation(0, 4)          # noise on a near-empty page


def test_good_words_ignores_weak_and_symbol_words():
    ws = [OCRWord(text="Unit", bbox=None, confidence=0.9), OCRWord(text="|", bbox=None, confidence=0.95),
          OCRWord(text="BA", bbox=None, confidence=0.4), OCRWord(text="No", bbox=None, confidence=0.8)]
    assert good_words([OCRLine(text="x", words=ws, bbox=None, confidence=0.8)]) == 2


class _FakeEngine:
    """Reads text only when the page it is given is upright (by the same projection test the reader uses)."""
    _is_loaded = True

    def __init__(self):
        self.seen = []

    def _ocr_image(self, img, index, psm=6):
        self.seen.append(img.size)
        upright = sideways_ratio(np.array(img.convert("L"))) >= 0.7
        n = 60 if upright else 2
        words = [OCRWord(text=f"word{i}", bbox=BoundingBox(x1=10 + i, y1=10, x2=60 + i, y2=30), confidence=0.9)
                 for i in range(n)]
        return [OCRLine(text=" ".join(w.text for w in words), words=words,
                        bbox=BoundingBox(x1=10, y1=10, x2=60 + n, y2=30), confidence=0.9)]


def _reader(engine):
    return PageReader(None, None, engine)


def test_sideways_page_is_read_turned_and_boxes_come_back_in_page_pixels():
    page = _text_page().transpose(Image.ROTATE_90).convert("RGB")      # 1200 wide x 900 tall, text runs vertically
    eng = _FakeEngine()
    lines = _reader(eng)._ocr_clean(page, 0)
    assert len(lines[0].words) == 60                                   # the turned read was kept
    assert any(sz == (900, 1200) for sz in eng.seen)                   # and it really OCR'd the turned page
    for w in lines[0].words:
        assert 0 <= w.bbox.x1 < w.bbox.x2 <= page.width and 0 <= w.bbox.y1 < w.bbox.y2 <= page.height


def test_upright_page_costs_no_extra_pass():
    page = _text_page().convert("RGB")
    eng = _FakeEngine()
    lines = _reader(eng)._ocr_clean(page, 0)
    assert len(eng.seen) == 1 and len(lines[0].words) == 60


def test_turn_that_does_not_help_is_not_kept(monkeypatch):
    page = _text_page().transpose(Image.ROTATE_90).convert("RGB")

    class _Blind(_FakeEngine):
        def _ocr_image(self, img, index, psm=6):                       # reads the same little no matter how it is turned
            self.seen.append(img.size)
            w = [OCRWord(text="abcd", bbox=BoundingBox(x1=1, y1=1, x2=30, y2=10), confidence=0.9) for _ in range(20)]
            return [OCRLine(text="abcd", words=w, bbox=BoundingBox(x1=1, y1=1, x2=30, y2=10), confidence=0.9)]

    eng = _Blind()
    lines = _reader(eng)._ocr_clean(page, 0)
    assert lines[0].words[0].bbox.x1 == 1                              # untouched original read, no mapping applied
    assert len(eng.seen) >= 2                                          # but it did try the turn
