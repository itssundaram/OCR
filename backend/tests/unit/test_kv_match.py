from app.ocr.kv_discovery import DiscoveredPair
from app.ocr.kv_match import match_fields


def P(label, value, x=10, y=10, page=1, conf=0.9):
    return DiscoveredPair(label=label, value=value, confidence=conf, page_number=page,
                          bbox={"x1": x, "y1": y, "x2": x + 50, "y2": y + 10})


def test_exact_and_spacing_and_synonyms():
    pairs = [P("IVNo", "IV/1"), P("Dated", "10 Jun 2026"), P("Chassis No", "MA5E"), P("Remarks", "x")]
    fields = [{"name": "iv_no"}, {"name": "date"}, {"name": "chasis_no"}, {"name": "missing_thing"}]
    m = match_fields(fields, pairs)
    assert m["iv_no"].pair.value == "IV/1"
    assert m["date"].pair.value == "10 Jun 2026"
    assert m["chasis_no"].pair.value == "MA5E"
    assert "missing_thing" not in m


def test_aliases_and_label():
    pairs = [P("Consignee", "413 Engr Sqn")]
    assert match_fields([{"name": "consign_to", "aliases": ["Consignee"]}], pairs)["consign_to"].pair.value == "413 Engr Sqn"
    assert match_fields([{"name": "x", "label": "CONSIGNEE"}], pairs)["x"].score == 1.0


def test_single_word_subset_is_not_a_match():
    pairs = [P("Parent Unit", "6 JAK RIF")]
    assert match_fields([{"name": "unit"}], pairs) == {}


def test_multiword_subset_matches_veh_ba_no():
    pairs = [P("BA No", "20D-210124N")]
    assert match_fields([{"name": "veh_ba_no"}], pairs)["veh_ba_no"].pair.value == "20D-210124N"


def test_left_right_hint_and_one_pair_per_field():
    pairs = [P("Unit", "CAFVD", x=900), P("Unit", "440 Engr", x=50)]
    fields = [{"name": "unit_right_side_of_the_page"}, {"name": "unit_left_side_of_the_page"}]
    m = match_fields(fields, pairs)
    assert m["unit_right_side_of_the_page"].pair.value == "CAFVD"
    assert m["unit_left_side_of_the_page"].pair.value == "440 Engr"


def test_date_field_needs_a_digit_and_table_fields_skipped():
    pairs = [P("Date", "Unknown"), P("Date", "12 May 2026", y=40)]
    assert match_fields([{"name": "date", "type": "date"}], pairs)["date"].pair.value == "12 May 2026"
    assert match_fields([{"name": "items", "type": "table"}], [P("Items", "x")]) == {}


def test_ocr_typo_tolerated():
    pairs = [P("Chasis Nc", "MA5E")]
    assert "chasis_no" in match_fields([{"name": "chasis_no"}], pairs)


def test_different_word_is_not_a_typo():
    assert match_fields([{"name": "consign_to"}], [P("Consignor", "Comdt ESD")]) == {}
    assert match_fields([{"name": "engine_no"}], [P("engine", "89500 KM")]) == {}
