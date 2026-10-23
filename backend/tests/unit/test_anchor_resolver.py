"""Anchor resolver + versioned store (pure logic, no OCR / DB)."""
import pytest

from app.anchors import (
    AnchorConflict, find_label_near, merge_anchor_fields, remove_anchor_field,
    resolve_anchor, rollback, value_shape, words_from_detections,
)


def W(text, x1, y1, x2, y2, conf=0.9):
    return {"text": text, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "conf": conf}


def page(dx=0, dy=0):
    """A 1000x1400 page: 'Engine No :' label + value, and a decoy line below."""
    return [
        W("Engine", 100 + dx, 300 + dy, 170 + dx, 330 + dy),
        W("No", 175 + dx, 300 + dy, 210 + dx, 330 + dy),
        W(":", 212 + dx, 300 + dy, 220 + dx, 330 + dy),
        W("WD4", 240 + dx, 300 + dy, 300 + dx, 330 + dy),
        W("-", 305 + dx, 300 + dy, 315 + dx, 330 + dy),
        W("299332", 320 + dx, 300 + dy, 420 + dx, 330 + dy),
        W("Chassis", 100 + dx, 360 + dy, 180 + dx, 390 + dy),
        W("No", 185 + dx, 360 + dy, 215 + dx, 390 + dy),
        W("MA5ABC123456", 240 + dx, 360 + dy, 460 + dx, 390 + dy),
    ]


ANCHOR_BOX = {"x1": 235, "y1": 295, "x2": 430, "y2": 335}


def test_reads_value_from_box():
    la, _ = merge_anchor_fields({}, {"engine_no": {"bbox": ANCHOR_BOX, "page_number": 1}},
                                page_w=1000, page_h=1400, verified=True, words_by_page=[page()])
    a = la["fields"]["engine_no"]
    assert a["label_text"].lower().startswith("engine no")
    rd = resolve_anchor(a, [page()], [(1000, 1400)])
    assert rd.value == "WD4 - 299332"
    assert rd.valid and rd.page_number == 1
    assert rd.bbox["x1"] >= 235 and rd.bbox["x2"] <= 430


def test_landmark_alignment_follows_shifted_scan():
    la, _ = merge_anchor_fields({}, {"engine_no": {"bbox": ANCHOR_BOX}}, page_w=1000, page_h=1400,
                                words_by_page=[page()])
    shifted = page(dx=25, dy=40)  # whole print moved
    rd = resolve_anchor(la["fields"]["engine_no"], [shifted], [(1000, 1400)])
    assert rd.aligned and rd.value == "WD4 - 299332"
    # without alignment info the same shifted page would read the decoy row or nothing useful
    anchor_no_label = {k: v for k, v in la["fields"]["engine_no"].items()
                       if k not in ("label_text", "label_rel_bbox")}
    rd2 = resolve_anchor(anchor_no_label, [shifted], [(1000, 1400)])
    assert rd2 is None or rd2.value != "WD4 - 299332" or not rd2.aligned


def test_page_size_change_uses_relative_box():
    la, _ = merge_anchor_fields({}, {"engine_no": {"bbox": ANCHOR_BOX}}, page_w=1000, page_h=1400,
                                words_by_page=[page()])
    # same document rendered at 2x
    big = [{**w, **{k: w[k] * 2 for k in ("x1", "y1", "x2", "y2")}} for w in page()]
    rd = resolve_anchor(la["fields"]["engine_no"], [big], [(2000, 2800)])
    assert rd.value == "WD4 - 299332"


def test_regex_and_blacklist_mark_invalid():
    la, _ = merge_anchor_fields({}, {"engine_no": {"bbox": ANCHOR_BOX}}, page_w=1000, page_h=1400,
                                words_by_page=[page()])
    a = la["fields"]["engine_no"]
    bad = resolve_anchor({k: v for k, v in a.items() if k != "shape"}, [page()], [(1000, 1400)],
                         spec={"regex": r"^\d{9}$"})
    assert bad is not None and not bad.valid and bad.confidence <= 0.3
    # with a learned shape the generic regex must not truncate the confirmed value
    kept = resolve_anchor(a, [page()], [(1000, 1400)], spec={"regex": r"[A-Z0-9/\-]{6,16}"})
    assert kept.value == "WD4 - 299332"
    bl = resolve_anchor(a, [page()], [(1000, 1400)], spec={"blacklist": ["wd4"]})
    assert not bl.valid


def test_empty_box_returns_none():
    anchor = {"bbox": {"x1": 600, "y1": 900, "x2": 800, "y2": 940}, "page_number": 1}
    assert resolve_anchor(anchor, [page()], [(1000, 1400)]) is None


def test_value_shape_and_label_helper():
    assert value_shape("WD4 - 299332") == "A9-9"
    lab = find_label_near(page(), ANCHOR_BOX, 1000)
    assert lab and "engine" in lab["text"].lower()


def test_merge_keeps_unrelated_fields_and_versions():
    la1, rep1 = merge_anchor_fields({}, {"a": {"bbox": ANCHOR_BOX, "value": "X1"}}, page_w=1000, page_h=1400, verified=True)
    la2, rep2 = merge_anchor_fields(la1, {"b": {"bbox": {"x1": 1, "y1": 1, "x2": 50, "y2": 20}}}, page_w=1000, page_h=1400, verified=True)
    assert set(la2["fields"]) == {"a", "b"} and rep2["kept"] == ["a"]
    assert (la1["version"], la2["version"]) == (1, 2)
    assert la2["fields"]["a"]["page_number"] == 1 and "rel_bbox" in la2["fields"]["a"]


def test_version_conflict():
    la1, _ = merge_anchor_fields({}, {"a": {"bbox": ANCHOR_BOX}}, page_w=1000, page_h=1400)
    with pytest.raises(AnchorConflict):
        merge_anchor_fields(la1, {"a": {"bbox": ANCHOR_BOX}}, page_w=1000, page_h=1400, expected_version=0)


def test_remove_and_rollback():
    la1, _ = merge_anchor_fields({}, {"a": {"bbox": ANCHOR_BOX}, "b": {"bbox": ANCHOR_BOX}}, page_w=1000, page_h=1400, verified=True)
    la2 = remove_anchor_field(la1, "b")
    assert set(la2["fields"]) == {"a"}
    la3 = rollback(la2)
    assert set(la3["fields"]) == {"a", "b"}


def test_moved_box_drops_stale_label_and_value():
    la1, _ = merge_anchor_fields({}, {"x": {"bbox": ANCHOR_BOX, "value": "OLD"}}, page_w=1000, page_h=1400,
                                 words_by_page=[page()])
    moved = {"x1": 235, "y1": 355, "x2": 470, "y2": 395}
    la2, _ = merge_anchor_fields(la1, {"x": {"bbox": moved, "value": ""}}, page_w=1000, page_h=1400,
                                 words_by_page=[page()])
    a = la2["fields"]["x"]
    assert a["label_text"].lower().startswith("chassis")
    assert a["value"] == "MA5ABC123456"


def test_detections_to_pages():
    dets = [{"text": "A", "bbox": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}, "confidence": 0.9, "page_number": 2}]
    pg = words_from_detections(dets, 1)
    assert len(pg) == 2 and pg[1][0]["text"] == "A"


def test_label_found_across_wide_gap_and_colon():
    # real form layout: 'STR No.' ... ':' ... value, with hundreds of px between label and value
    words = [W("STR", 192, 564, 257, 588), W("No.", 279, 564, 335, 590), W(":", 663, 581, 670, 599),
             W("19120893", 704, 575, 873, 603)]
    lab = find_label_near(words, {"x1": 704, "y1": 575, "x2": 873, "y2": 603}, 2523)
    assert lab and lab["text"] == "STR No"


def test_shift_measured_elsewhere_is_applied_when_own_label_missing():
    la, _ = merge_anchor_fields({}, {"engine_no": {"bbox": ANCHOR_BOX}}, page_w=1000, page_h=1400, words_by_page=[page()])
    a = {k: v for k, v in la["fields"]["engine_no"].items() if k not in ("label_text", "label_rel_bbox")}
    shifted = page(dx=25, dy=40)
    assert resolve_anchor(a, [shifted], [(1000, 1400)]) is None or resolve_anchor(a, [shifted], [(1000, 1400)]).value != "WD4 - 299332"
    rd = resolve_anchor(a, [shifted], [(1000, 1400)], shift=(25, 40))
    assert rd and rd.value == "WD4 - 299332" and rd.aligned
