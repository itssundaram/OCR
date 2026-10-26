"""A handwriting model that cannot load (low RAM, missing weights) must not fail the job."""
from PIL import Image

import app.ocr.handwriting_ocr as hw


def test_failed_load_returns_empty_and_is_not_retried(monkeypatch):
    monkeypatch.setattr(hw, "_LOAD_FAILED", False)
    calls = {"n": 0}

    def boom(self):
        calls["n"] += 1
        raise RuntimeError("Failed to load handwriting model: not enough memory")

    monkeypatch.setattr(hw.HandwritingOCREngine, "load_model", boom)
    eng = hw.HandwritingOCREngine()
    img = Image.new("RGB", (40, 20), "white")
    assert eng.process_region(img) == ("", 0.0)
    assert eng.process_region(img) == ("", 0.0)
    assert calls["n"] == 1                     # one attempt, then the engine stays off for this process
    assert hw.HandwritingOCREngine().process_region(img) == ("", 0.0)   # also for a new engine object
    assert calls["n"] == 1
