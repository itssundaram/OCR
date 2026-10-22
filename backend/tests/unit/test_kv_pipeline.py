"""Pairs-first behaviour of the learned_anchor pipeline (no OCR, no models)."""
from PIL import Image

from app.ocr.base import BoundingBox, OCRLine, OCRPage, OCRResult, OCRWord
from app.pipelines.base import FieldResult, PipelineResult
from app.pipelines.learned_anchor_pipeline import LearnedAnchorPipeline


def _line(words, y):
    ws, x = [], 10
    for w in words:
        ws.append(OCRWord(text=w, bbox=BoundingBox(x1=x, y1=y, x2=x + len(w) * 10, y2=y + 14), confidence=0.9))
        x += len(w) * 10 + 8
    return OCRLine(text=" ".join(words), words=ws, bbox=BoundingBox(x1=10, y1=y, x2=x, y2=y + 14), confidence=0.9)


class _Eng:
    def __init__(self, lines):
        self.res = OCRResult(pages=[OCRPage(page_number=1, width=800, height=1000, lines=lines)], engine_name="test", engine_version="0")

    def process_images(self, imgs):
        return self.res


def _pipeline(lines):
    p = LearnedAnchorPipeline()
    eng = _Eng(lines)
    p._get_ocr_engine = lambda: eng
    return p


PAGE = [Image.new("RGB", (800, 1000))]
LINES = [
    _line(["IV", "No:", "IV/CS/03"], 10),
    _line(["Unit:", "100", "Fd", "Regt"], 40),
    _line(["Remarks:", "none", "here"], 70),
]


def test_all_fields_matched_returns_immediately():
    p = _pipeline(LINES)
    kv = p._kv_scan(PAGE)
    res = p._kv_first_result(kv, [{"name": "iv_no"}, {"name": "unit"}], PAGE)
    assert res is not None and res.metadata["mode"] == "KV_FIRST"
    by = {f.field_name: f for f in res.fields}
    assert by["iv_no"].value == "IV/CS/03" and by["iv_no"].source == ""
    assert by["unit"].value == "100 Fd Regt"
    # the unrelated pair is still listed, as a discovered field
    assert by["remarks"].source == "discovered"
    assert len([f for f in res.fields if f.source == ""]) == 2  # matched pairs are not repeated


def test_missing_field_falls_through():
    p = _pipeline(LINES)
    kv = p._kv_scan(PAGE)
    assert p._kv_first_result(kv, [{"name": "iv_no"}, {"name": "rv_no"}], PAGE) is None


def test_low_ocr_confidence_falls_through():
    low = [_line(["IV", "No:", "IV/CS/03"], 10)]
    for w in low[0].words:
        w.confidence = 0.2
    p = _pipeline(low)
    assert p._kv_first_result(p._kv_scan(PAGE), [{"name": "iv_no"}], PAGE) is None


def test_fallback_result_gets_gaps_filled_but_keeps_existing_values():
    p = _pipeline(LINES)
    kv = p._kv_scan(PAGE)
    result = PipelineResult(pipeline_name="learned_anchor", status="completed", fields=[
        FieldResult(field_name="iv_no", value="FROM-VLM", confidence=0.7, page_number=1),
        FieldResult(field_name="unit", value="", confidence=0.0, page_number=1),
    ])
    p._attach_discovered_pairs(result, kv, [{"name": "iv_no"}, {"name": "unit"}])
    by = {f.field_name: f for f in result.fields}
    assert by["iv_no"].value == "FROM-VLM"            # existing value untouched
    assert by["unit"].value == "100 Fd Regt"          # empty one filled from the pair
    assert by["unit"].extraction_method == "kv_match"
    assert by["remarks"].source == "discovered"


def test_discovery_disabled_returns_none(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "KV_DISCOVERY_ENABLED", False)
    assert _pipeline(LINES)._kv_scan(PAGE) is None
