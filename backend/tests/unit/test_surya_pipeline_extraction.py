"""
Phase 6 — tests for _run_surya_on_page, the function extracted out of
app/services/extractor.py's inline OCR_STRATEGY=="surya"/"surya_with_fallback"
branch (see that module for the full history/rationale).

These test the EXTRACTED ORCHESTRATION LOGIC ONLY (region iteration,
confidence averaging, table collection) with fake layout/OCR engines —
exactly like this project avoids mocking Oracle, there is no practical way
to unit-test real Surya/Tesseract model inference without a GPU and the
actual model weights, so that part stays verified only by py_compile and
manual trace-through, same as every other OCR-engine call in this codebase.
"""
from PIL import Image

from app.ocr.base import BoundingBox, OCRLine
from app.ocr.surya_layout_engine import LayoutRegion
from app.ocr.region_processor import OCRRegionResult
import app.services.extractor as extractor_module


class _FakeLayoutEngine:
    def __init__(self, regions):
        self._regions = regions

    def detect_layout(self, img):
        return self._regions


def _region(region_type="Text"):
    return LayoutRegion(
        region_type=region_type,
        bbox=BoundingBox(x1=0, y1=0, x2=10, y2=10),
        confidence=1.0,
        cropped_image=Image.new("RGB", (10, 10), "white"),
    )


def test_run_surya_on_page_averages_confidence_across_regions(monkeypatch):
    regions = [_region(), _region()]
    fake_results = [
        OCRRegionResult(region_type="Text", lines=[OCRLine(text="a", confidence=0.9)], confidence=0.9),
        OCRRegionResult(region_type="Text", lines=[OCRLine(text="b", confidence=0.5)], confidence=0.5),
    ]

    monkeypatch.setattr(
        extractor_module, "_get_surya_engines",
        lambda: (_FakeLayoutEngine(regions), object(), object()),
    )
    monkeypatch.setattr(extractor_module, "preprocess_page", lambda img: img)

    call_count = {"n": 0}

    def fake_process_region(region, ocr_engine, hw_engine, llm_engine):
        result = fake_results[call_count["n"]]
        call_count["n"] += 1
        return result

    monkeypatch.setattr(extractor_module, "process_region", fake_process_region)

    img = Image.new("RGB", (100, 100), "white")
    lines, tables, avg_conf, ocr_method, width, height = extractor_module._run_surya_on_page(img)

    assert ocr_method == "surya"
    assert len(lines) == 2
    assert {ln.text for ln in lines} == {"a", "b"}
    assert avg_conf == 0.7  # (0.9 + 0.5) / 2
    assert tables == []  # neither fake region produced a table
    assert (width, height) == (100, 100)


def test_run_surya_on_page_collects_table_results(monkeypatch):
    from app.ocr.base import OCRTable

    region = _region(region_type="Table")
    table = OCRTable(headers=["A"], rows=[["1"]])
    fake_result = OCRRegionResult(region_type="Table", lines=[], confidence=0.8, table=table)

    monkeypatch.setattr(
        extractor_module, "_get_surya_engines",
        lambda: (_FakeLayoutEngine([region]), object(), object()),
    )
    monkeypatch.setattr(extractor_module, "preprocess_page", lambda img: img)
    monkeypatch.setattr(extractor_module, "process_region", lambda *a, **k: fake_result)

    img = Image.new("RGB", (50, 50), "white")
    lines, tables, avg_conf, ocr_method, width, height = extractor_module._run_surya_on_page(img)

    assert tables == [table]
    assert avg_conf == 0.8


def test_run_surya_on_page_falls_back_to_tesseract_engine_when_requested(monkeypatch):
    # use_tesseract_fallback=True must swap in TesseractOCREngine as the
    # handwriting/low-confidence fallback engine instead of TrOCR — mirrors
    # what OCR_STRATEGY=="surya_with_fallback" already selected inline.
    region = _region()
    seen_engines = {}

    def fake_process_region(reg, ocr_engine, hw_engine, llm_engine):
        seen_engines["hw_engine_type"] = type(hw_engine).__name__
        return OCRRegionResult(region_type="Text", lines=[], confidence=1.0)

    monkeypatch.setattr(
        extractor_module, "_get_surya_engines",
        lambda: (_FakeLayoutEngine([region]), object(), "placeholder_hw_engine"),
    )
    monkeypatch.setattr(extractor_module, "preprocess_page", lambda img: img)
    monkeypatch.setattr(extractor_module, "process_region", fake_process_region)

    img = Image.new("RGB", (20, 20), "white")
    extractor_module._run_surya_on_page(img, use_tesseract_fallback=True)

    assert seen_engines["hw_engine_type"] == "TesseractOCREngine"


def test_run_surya_on_page_treats_whole_page_as_one_region_when_layout_disabled(monkeypatch):
    # layout_engine=None (SURYA_LAYOUT_ENABLED=False) must fall back to a
    # single full-page Text region instead of crashing.
    monkeypatch.setattr(
        extractor_module, "_get_surya_engines",
        lambda: (None, object(), object()),
    )
    monkeypatch.setattr(extractor_module, "preprocess_page", lambda img: img)
    monkeypatch.setattr(
        extractor_module, "process_region",
        lambda *a, **k: OCRRegionResult(region_type="Text", lines=[OCRLine(text="whole page", confidence=1.0)], confidence=1.0),
    )

    img = Image.new("RGB", (64, 48), "white")
    lines, tables, avg_conf, ocr_method, width, height = extractor_module._run_surya_on_page(img)

    assert len(lines) == 1
    assert avg_conf == 1.0
