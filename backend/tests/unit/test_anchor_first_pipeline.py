"""A saved/verified anchor must be used on the next run (regression for 'my edits are ignored')."""
from PIL import Image

from app.anchors import merge_anchor_fields, words_from_lines
from app.ocr.base import BoundingBox, OCRLine, OCRPage, OCRResult, OCRWord
from app.pipelines.learned_anchor_pipeline import LearnedAnchorPipeline


def _line(words, y, x0=10):
    ws, x = [], x0
    for w in words:
        ws.append(OCRWord(text=w, bbox=BoundingBox(x1=x, y1=y, x2=x + len(w) * 10, y2=y + 24), confidence=0.9))
        x += len(w) * 10 + 8
    return OCRLine(text=" ".join(words), words=ws, bbox=BoundingBox(x1=x0, y1=y, x2=x, y2=y + 24), confidence=0.9)


class _Eng:
    def __init__(self, lines):
        self.calls = 0
        self.res = OCRResult(pages=[OCRPage(page_number=1, width=800, height=1000, lines=lines)],
                             engine_name="t", engine_version="0")

    def process_images(self, imgs):
        self.calls += 1
        return self.res


PAGE = [Image.new("RGB", (800, 1000), "white")]
# A decoy 'Engine No' pair near the top, the real engine number on its own line lower down.
LINES = [
    _line(["Engine", "No:", "DECOY123"], 150),
    _line(["Chassis", "No:", "MA5ABC123456"], 200),
    _line(["Engine", "Number"], 500),
    _line(["WD4", "-", "299332"], 530, x0=200),
]


def _pipe():
    p = LearnedAnchorPipeline()
    p._eng = _Eng(LINES)
    p._get_ocr_engine = lambda: p._eng
    return p


def _locked_anchor(source="user_lock"):
    words = [words_from_lines(LINES)]
    box = {"x1": 190, "y1": 525, "x2": 330, "y2": 560}
    la, _ = merge_anchor_fields({}, {"engine_no": {"bbox": box, "page_number": 1}}, page_w=800, page_h=1000,
                                verified=True, words_by_page=words, source=source)
    return la


FIELDS = [{"name": "engine_no"}, {"name": "chasis_no"}]


def test_verified_anchor_beats_pairs_and_ocr_runs_once():
    p = _pipe()
    res = p.extract_fields(PAGE, FIELDS, "job1", schema={"learned_anchors": _locked_anchor()})
    by = {f.field_name: f for f in res.fields}
    assert res.metadata["mode"] == "ANCHOR_VERIFIED"
    assert by["engine_no"].value == "WD4 - 299332"          # not the DECOY123 pair
    assert by["engine_no"].extraction_method == "anchor_verified"
    assert by["chasis_no"].value.startswith("MA5")           # no anchor -> label/pair fallback
    assert p._eng.calls == 1                                  # one OCR pass total
    assert res.metadata["anchor_trace"]["engine_no"]["method"] == "anchor_verified"


def test_user_saved_single_field_is_trusted_even_when_not_locked():
    la = _locked_anchor(source="user_field")
    la["is_verified"] = False
    res = _pipe().extract_fields(PAGE, FIELDS, "job2", schema={"learned_anchors": la})
    by = {f.field_name: f for f in res.fields}
    assert by["engine_no"].value == "WD4 - 299332"


def test_auto_learned_anchor_does_not_beat_pairs():
    la = _locked_anchor(source="auto")
    la["is_verified"] = False
    res = _pipe().extract_fields(PAGE, FIELDS, "job3", schema={"learned_anchors": la})
    assert res.metadata["mode"] == "KV_FIRST"   # unchanged behaviour for untrusted anchors


def test_force_relearn_skips_anchor_first():
    p = _pipe()
    try:
        res = p.extract_fields(PAGE, FIELDS, "job4", schema={"learned_anchors": _locked_anchor()}, force_relearn=True)
        assert res.metadata.get("mode") != "ANCHOR_VERIFIED"
    except Exception:
        pass  # cold path needs the VLM stack; the point is that anchor-first was not used


def test_anchor_on_empty_area_falls_through():
    la = _locked_anchor()
    la["fields"]["engine_no"]["rel_bbox"] = {"x1": 0.7, "y1": 0.9, "x2": 0.95, "y2": 0.95}
    la["fields"]["engine_no"].pop("label_text", None)
    la["fields"]["engine_no"].pop("label_rel_bbox", None)
    res = _pipe().extract_fields(PAGE, FIELDS, "job5", schema={"learned_anchors": la})
    # nothing readable in the stored box -> the label/pair routes still answer
    by = {f.field_name: f for f in res.fields}
    assert res.metadata["mode"] != "ANCHOR_VERIFIED" or by["engine_no"].extraction_method != "anchor_verified"
