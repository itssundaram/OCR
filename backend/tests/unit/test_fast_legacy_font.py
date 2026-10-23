"""Legacy Hindi font (Kruti Dev) text is dropped from the native text layer."""
import pymupdf as fitz

from app.fast.triage import drop_legacy_words, legacy_font_rects


class _Page:
    def __init__(self, fonts, spans):
        self._fonts, self._spans = fonts, spans

    def get_fonts(self):
        return [(1, "ttf", "TrueType", f, "F1", "Identity-H") for f in self._fonts]

    def get_text(self, kind):
        assert kind == "dict"
        return {"blocks": [{"lines": [{"spans": [{"font": f, "bbox": b, "text": t} for f, b, t in self._spans]}]}]}


def test_no_legacy_font_means_no_dict_scan():
    page = _Page(["Arial", "Times New Roman"], [])
    assert legacy_font_rects(page) == []


def test_legacy_spans_are_found_and_their_words_dropped():
    page = _Page(["Arial", "ABCDEE+Kruti Dev 010"],
                 [("ABCDEE+Kruti Dev 010", (10, 10, 200, 30), "lsuk lfpo"), ("Arial", (10, 40, 200, 60), "Military Secretary")])
    rects = legacy_font_rects(page)
    assert len(rects) == 1
    raw = [(10, 10, 60, 30, "lsuk"), (70, 10, 120, 30, "lfpo"), (10, 40, 90, 60, "Military"), (95, 40, 190, 60, "Secretary")]
    kept = [w[4] for w in drop_legacy_words(raw, rects)]
    assert kept == ["Military", "Secretary"]


def test_real_page_with_no_legacy_font_is_untouched():
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 100), "Reporting for duty", fontsize=12)
    assert legacy_font_rects(pg) == []
