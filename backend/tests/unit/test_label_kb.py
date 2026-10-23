from app.ocr import label_kb
from app.ocr.kv_discovery import DiscoveredPair
from app.ocr.kv_match import _tokens, match_fields


def _pair(label, value, y=100, x=50):
    return DiscoveredPair(label=label, value=value, confidence=0.9, page_number=1,
                          bbox={"x1": x, "y1": y, "x2": x + 80, "y2": y + 20})


def test_same_concept_is_symmetric_and_ignores_case_and_punctuation():
    assert label_kb.same_concept(_tokens("unit"), _tokens("FROM:"))
    assert label_kb.same_concept(_tokens("From"), _tokens("Issued By"))
    assert label_kb.same_concept(_tokens("Dated"), _tokens("Date of issue"))
    assert not label_kb.same_concept(_tokens("unit"), _tokens("rank"))
    assert not label_kb.same_concept(_tokens("unit"), _tokens("unit"))      # identical is an exact match, not a synonym


def test_field_is_found_under_a_synonym_label():
    pairs = [_pair("From", "9 ASSAM", y=100), _pair("Date of issue", "22 May 26", y=140)]
    got = match_fields([{"name": "unit"}, {"name": "dated"}], pairs)
    assert got["unit"].pair.value == "9 ASSAM" and got["dated"].pair.value == "22 May 26"
    assert got["unit"].score < 1.0                                      # a synonym never outranks an exact label


def test_exact_label_beats_synonym_when_both_are_printed():
    pairs = [_pair("From", "SENDER UNIT", y=100), _pair("Unit", "9 ASSAM", y=140)]
    got = match_fields([{"name": "unit"}, {"name": "from"}], pairs)
    assert got["unit"].pair.value == "9 ASSAM"
    assert got["from"].pair.value == "SENDER UNIT"


def test_subset_labels_still_match_ba_no_in_veh_ba_no():
    got = match_fields([{"name": "ba_no"}], [_pair("Veh BA No", "20D-210124N")])
    assert got["ba_no"].pair.value == "20D-210124N"


def test_unrelated_labels_do_not_match():
    got = match_fields([{"name": "unit"}], [_pair("Rank", "Hav"), _pair("Station", "c/o 99 APO")])
    assert "unit" not in got


def test_aliases_are_learned_from_a_confirmed_anchor_once():
    fields = [{"name": "unit", "aliases": []}, {"name": "dated"}]
    anchors = {"unit": {"label_text": "From :"}, "dated": {"label_text": "Date"}}
    added = label_kb.learn_aliases_from_anchors(fields, anchors)
    # "From" is a known synonym of unit, "Date" matches dated: nothing to learn
    assert added == []
    anchors = {"unit": {"label_text": "Formation Hq :"}}
    added = label_kb.learn_aliases_from_anchors(fields, anchors)
    assert added == [{"field": "unit", "alias": "Formation Hq"}]
    assert fields[0]["aliases"] == ["Formation Hq"]
    assert label_kb.learn_aliases_from_anchors(fields, anchors) == []     # second time: already known
    got = match_fields(fields, [_pair("Formation Hq", "HQ 5 CORPS")])
    assert got["unit"].pair.value == "HQ 5 CORPS"


def test_operator_file_extends_the_groups(tmp_path, monkeypatch):
    from app.core.config import settings
    f = tmp_path / "syn.json"
    f.write_text('[["consignment id", "waybill no"]]', encoding="utf-8")
    monkeypatch.setattr(settings, "LABEL_SYNONYMS_FILE", str(f), raising=False)
    label_kb.reload()
    try:
        assert label_kb.same_concept(_tokens("Consignment ID"), _tokens("Waybill No"))
    finally:
        monkeypatch.setattr(settings, "LABEL_SYNONYMS_FILE", None, raising=False)
        label_kb.reload()
