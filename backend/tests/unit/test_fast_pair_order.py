"""Discovered pairs stay in page order however the page threads finish (so engine_no vs engine_no_2 is stable)."""
from app.fast.events import EventBus
from app.fast.fields import FieldExtractor
from app.fast.types import PageData
from app.ocr.base import BoundingBox, OCRLine, OCRWord


def _line(parts, y):
    words = [OCRWord(text=t, bbox=BoundingBox(x1=x, y1=y, x2=x + w, y2=y + 30), confidence=0.9) for t, x, w in parts]
    return OCRLine(text=" ".join(w.text for w in words), words=words,
                   bbox=BoundingBox(x1=words[0].bbox.x1, y1=y, x2=words[-1].bbox.x2, y2=y + 30), confidence=0.9)


def _page(n, value):
    # "Engine No : <value>"
    lines = [_line([("Engine", 100, 90), ("No", 200, 40), (":", 250, 10), (value, 280, 200)], 100)]
    return PageData(page_number=n, width=1000, height=1400, scale=1.0, source="ocr", lines=lines)


def _extractor():
    bus = EventBus("j", redis_conn=None)
    bus._redis = None
    return FieldExtractor([{"name": "engine_no"}], bus)


def test_pairs_end_up_in_page_order_whichever_page_finishes_first():
    a, b = _extractor(), _extractor()
    a.add_page(_page(1, "AAA111")); a.add_page(_page(2, "BBB222"))
    b.add_page(_page(2, "BBB222")); b.add_page(_page(1, "AAA111"))
    assert len(a.pairs) == 2 and len(b.pairs) == 2
    pages_a = [p.page_number for p in a.pairs]
    pages_b = [p.page_number for p in b.pairs]
    assert pages_a == sorted(pages_a) and pages_b == sorted(pages_b)
    assert [(p.page_number, p.value) for p in a.pairs] == [(p.page_number, p.value) for p in b.pairs]
