"""Value extents, page choice, per-page occurrences, anchor vetting, and OCR-input straightening/flattening."""
import cv2
import numpy as np
from PIL import Image

from app.fast.events import EventBus
from app.fast.fields import FieldExtractor
from app.fast.linefree import estimate_text_skew, flatten_illumination
from app.fast.types import PageData
from app.ocr.base import BoundingBox, OCRLine, OCRWord
from app.ocr.kv_match import match_fields
from app.ocr.kv_discovery import DiscoveredPair


def _line(parts, y, h=30, start=100, gap=14):
    """parts: [(text, width)] laid left to right with `gap` between words (use a big gap for a new column)."""
    x, words = start, []
    for item in parts:
        text, width = item[0], item[1]
        x += item[2] if len(item) > 2 else 0
        words.append(OCRWord(text=text, bbox=BoundingBox(x1=x, y1=y, x2=x + width, y2=y + h), confidence=0.9))
        x += width + gap
    return OCRLine(text=" ".join(w.text for w in words), words=words,
                   bbox=BoundingBox(x1=words[0].bbox.x1, y1=y, x2=words[-1].bbox.x2, y2=y + h), confidence=0.9)


def _page(n, lines, width=1000, height=1400):
    return PageData(page_number=n, width=width, height=height, scale=1.0, source="ocr", lines=lines)


def _extractor(fields):
    bus = EventBus("j", redis_conn=None)
    bus._redis = None
    return FieldExtractor([{"name": f} if isinstance(f, str) else f for f in fields], bus)


def test_value_stops_at_the_next_column_and_the_next_label():
    ex = _extractor(["dated", "rv_no"])
    # "Dated 2 Jun 2025        Dated  05 Jul"  -> the second column is not part of the first value
    page = _page(1, [_line([("Dated", 90), ("2", 20), ("Jun", 50), ("2025", 70), ("Dated", 90, 300), ("05", 30)], 100)])
    ex.pages[1] = page
    _, _, words = ex._proximity({"name": "dated"})
    assert [w.text for w in words] == ["2", "Jun", "2025"]
    # another field's one-word label ends the value as well, even with normal word spacing
    page2 = _page(1, [_line([("IV", 40), ("No", 40), ("20/4/MT/02", 160), ("RV", 40), ("No", 40), ("77", 30)], 100)])
    ex2 = _extractor(["iv_no", "rv_no"])
    ex2.pages[1] = page2
    _, _, words = ex2._proximity({"name": "iv_no"})
    assert [w.text for w in words] == ["20/4/MT/02"]


def test_a_label_that_opens_its_row_beats_the_same_words_inside_a_sentence():
    ex = _extractor(["ba_no"])
    sentence = _line([("Issued", 80), ("to", 30), ("Back", 60), ("loading", 90), ("Veh", 50), ("BA", 40), ("No", 40), ("14B", 60), ("XYZ", 60)], 100)
    row = _line([("Veh", 50), ("BA", 40), ("No", 40), ("-", 10), ("12D", 60), ("190734L", 120)], 300)
    ex.pages[1] = _page(1, [sentence, row])
    _, box, words = ex._proximity({"name": "ba_no"})
    assert box["y1"] == 300 and [w.text for w in words] == ["12D", "190734L"]


def test_a_longer_label_is_not_the_short_one():
    ex = _extractor(["date"])
    longer = _line([("Date", 60), ("of", 30), ("Final", 60), ("Insp", 60), ("30", 30), ("May", 50)], 100)
    real = _line([("Date", 60), (":", 10), ("2", 20), ("Jun", 50), ("2025", 70)], 400)
    ex.pages[1] = _page(1, [longer, real])
    _, box, words = ex._proximity({"name": "date"})
    assert box["y1"] == 400


def test_a_wrapped_value_takes_its_second_line():
    ex = _extractor(["auth"])
    first = _line([("Auth", 70), ("Final", 70), ("Insp", 60), ("report", 90), ("dt", 30), ("23", 40), ("Inf", 40), ("DOU", 700)], 100)
    second = _line([("disposal", 100), ("order", 80), ("No", 40), ("230604", 120)], 140, start=160)
    third = _line([("Ser", 50), ("No", 40)], 260)
    ex.pages[1] = _page(1, [first, second, third], width=1200)
    _, _, words = ex._proximity({"name": "auth"})
    assert [w.text for w in words][-2:] == ["No", "230604"] and "Ser" not in [w.text for w in words]


def test_from_and_to_fields_take_the_left_and_right_label_of_the_same_name():
    left = DiscoveredPair("Unit", "2 KUMAON", 0.9, 1, {"x1": 150, "y1": 100, "x2": 300, "y2": 130}, {"x1": 100, "y1": 100, "x2": 140, "y2": 130})
    right = DiscoveredPair("Unit", "VMSD Chheoki", 0.9, 1, {"x1": 650, "y1": 100, "x2": 800, "y2": 130}, {"x1": 600, "y1": 100, "x2": 640, "y2": 130})
    m = match_fields([{"name": "unit_to"}, {"name": "unit_from"}], [right, left])
    assert m["unit_from"].pair.value == "2 KUMAON" and m["unit_to"].pair.value == "VMSD Chheoki"


def test_clear_read_on_a_later_page_beats_a_garbled_one_on_an_earlier_page():
    bad = DiscoveredPair("IV No", "20/a/M TIGNES", 0.4, 1, {"x1": 150, "y1": 100, "x2": 300, "y2": 130})
    good = DiscoveredPair("IV No", "20/4/MT/02", 0.95, 2, {"x1": 150, "y1": 100, "x2": 300, "y2": 130})
    assert match_fields([{"name": "iv_no"}], [bad, good])["iv_no"].pair.value == "20/4/MT/02"


def test_the_same_field_on_another_page_is_reported_as_an_occurrence():
    ex = _extractor(["iv_no"])
    ex.pages[1] = _page(1, [_line([("IV", 40), ("No", 40), (":", 10), ("20/4/MT/02", 160)], 100)])
    ex.pages[2] = _page(2, [_line([("IV", 40), ("No", 40), (":", 10), ("20/4/MT/09", 160)], 200)])
    for n in (1, 2):
        ex.pairs.extend(__import__("app.ocr.kv_discovery", fromlist=["x"]).discover_key_values([ex.pages[n].lines]))
        for p in ex.pairs:
            p.page_number = p.page_number if p.page_number == n else p.page_number
    ex.pairs[0].page_number, ex.pairs[1].page_number = 1, 2
    results = ex.finalize()
    r = next(x for x in results if x.field_name == "iv_no")
    assert r.value and len(r.occurrences) == 1 and r.occurrences[0]["page_number"] != r.page_number


def test_an_oversized_or_unverified_anchor_is_not_trusted():
    ex = _extractor(["auth"])
    rd = type("R", (), {"value": "Run by Chassis ... ISSUED BY", "extra": {"lines": 12, "has_label": True, "landmark": False}})()
    assert ex._vet_anchor({"name": "auth"}, rd, None) == (None, "anchor_box_too_large")
    rd2 = type("R", (), {"value": "Run by Chassis -116108Kms", "extra": {"lines": 2, "has_label": True, "landmark": False}})()
    m = type("M", (), {"pair": DiscoveredPair("Auth", "Final Insp report (Veh) IAF", 0.9, 1)})()
    assert ex._vet_anchor({"name": "auth"}, rd2, m)[1] == "anchor_label_not_found"
    ok = type("R", (), {"value": "Final Insp report", "extra": {"lines": 1, "has_label": True, "landmark": True}})()
    assert ex._vet_anchor({"name": "auth"}, ok, m)[0] is ok


def _text_image(angle=0.0, shade=False):
    img = np.full((1400, 1000), 255, np.uint8)
    for row in range(14):
        y = 120 + row * 85
        for col in range(5):
            x = 80 + col * 170
            cv2.putText(img, "Sample", (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.4, 0, 3)
    if shade:
        grad = np.tile(np.linspace(110, 255, 1000, dtype=np.float32), (1400, 1)).astype(np.float32)
        img = (img.astype(np.float32) * grad / 255).astype(np.uint8)
    M = cv2.getRotationMatrix2D((500, 700), angle, 1.0)
    return cv2.warpAffine(img, M, (1000, 1400), borderValue=255 if not shade else 200)


def test_text_tilt_is_found_without_any_ruled_table():
    # warpAffine(+a) turns the page counter-clockwise (text rises to the right); the page needs +(-a) to be straight
    angle, gain = estimate_text_skew(_text_image(3.0))
    assert abs(angle + 3.0) < 0.4 and gain > 1.3
    straight, g0 = estimate_text_skew(_text_image(0.0))
    assert abs(straight) < 0.25


def test_a_dark_gradient_is_flattened_and_a_white_page_is_left_alone():
    white = Image.fromarray(_text_image(0.0)).convert("RGB")
    out, changed = flatten_illumination(white)
    assert not changed and out is white
    shaded = Image.fromarray(_text_image(0.0, shade=True)).convert("RGB")
    flat, changed = flatten_illumination(shaded)
    assert changed
    g = np.array(flat.convert("L"))
    assert abs(float(g[:, :200].mean()) - float(g[:, -200:].mean())) < 25      # the left-to-right gradient is gone


def test_accuracy_harness_takes_the_reading_for_the_requested_page():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("accuracy_eval", pathlib.Path(__file__).resolve().parents[2] / "scripts" / "accuracy_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from app.pipelines.base import FieldResult
    fr = FieldResult(field_name="iv_no", value="20/4/MT/02", confidence=0.9, page_number=2,
                     occurrences=[{"page_number": 1, "value": "20/4 MT 02", "confidence": 0.5, "source": "label_proximity"}])
    assert mod.reading_on_page(fr, 2)[0] == "20/4/MT/02"
    assert mod.reading_on_page(fr, 1)[0] == "20/4 MT 02"
    assert mod.reading_on_page(fr, 3) is None
    assert mod.similarity("20/4/MT/02", "20-4-mt-02") == 1.0
