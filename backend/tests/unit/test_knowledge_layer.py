"""TMS / MMS / PSG knowledge layer: field vocabulary, left/right voucher columns, page types, table headers."""
from app.fast.events import EventBus
from app.fast.fields import FieldExtractor
from app.fast.types import PageData
from app.knowledge.kb import (_date_text, classify_page, enrich_fields, normalize_headers, table_records,
                              value_is_header, zone_ok)
from app.ocr.base import BoundingBox, OCRLine, OCRWord
from app.ocr.kv_discovery import DiscoveredPair
from app.ocr.kv_match import match_fields


def _pair(label, value, lx, vx, page=1, y=100):
    return DiscoveredPair(label=label, value=value, confidence=0.9, page_number=page,
                          bbox={"x1": vx, "y1": y, "x2": vx + 200, "y2": y + 30},
                          label_bbox={"x1": lx, "y1": y, "x2": lx + 60, "y2": y + 30})


W = {1: 1000}


def _fields(*names):
    return enrich_fields([{"name": n} for n in names])


def test_enrich_adds_labels_side_and_pattern_but_never_overrides():
    out = enrich_fields([{"name": "unit_from"}, {"name": "ba_no", "pattern": "^X"}, {"name": "weird"}, {"name": "t", "type": "table"}])
    uf, ba, weird, table = out
    assert uf["side"] == "left" and uf["exact_labels"] and uf["kb_id"] == "unit_from"
    assert ba["pattern"] == "^X" and "Veh BA No" in ba["aliases"]
    assert weird == {"name": "weird"} and table == {"name": "t", "type": "table"}


def test_two_units_go_to_their_own_columns():
    pairs = [_pair("Unit", "440 Engr Sqn", 160, 230), _pair("Unit", "CAFVD", 690, 740)]
    got = match_fields(_fields("unit_from", "unit_to"), pairs, W)
    assert got["unit_from"].pair.value == "440 Engr Sqn" and got["unit_to"].pair.value == "CAFVD"


def test_a_lone_right_unit_is_not_given_to_the_left_field():
    got = match_fields(_fields("unit_from", "unit_to"), [_pair("Unit", "VMSD Chheoki", 680, 740)], W)
    assert "unit_from" not in got and got["unit_to"].pair.value == "VMSD Chheoki"


def test_issued_to_fills_the_right_unit_when_only_the_left_unit_is_printed():
    pairs = [_pair("Unit", "15 Corps Engg Sig Regt", 150, 230), _pair("ISSUED TO", "DET RVP (NCVD)", 470, 560, y=200)]
    got = match_fields(_fields("unit_from", "unit_to"), pairs, W)
    assert got["unit_from"].pair.value.startswith("15 Corps") and got["unit_to"].pair.value == "DET RVP (NCVD)"


def test_issued_to_with_a_value_in_the_left_half_is_not_a_right_unit():
    got = match_fields(_fields("unit_to"), [_pair("Issued to", "Back loading of Ci-V", 270, 330)], W)
    assert "unit_to" not in got


def test_signature_footer_labels_are_not_unit_synonyms():
    got = match_fields(_fields("unit_from"), [_pair("Issued by", "DEPOSITED BY", 100, 300)], W)
    assert "unit_from" not in got


def test_a_header_word_is_not_a_value():
    pairs = [_pair("DOS", "DOB", 100, 160)]
    assert match_fields(_fields("dos"), pairs, W) == {}
    assert value_is_header("DOB /Unit") and not value_is_header("303 Fd Hosp")


def test_single_column_issue_voucher_uses_consignor_not_the_signature_block_unit():
    pairs = [_pair("Consignor", "Comdt ESD Delhi Cantt", 100, 250, y=300),
             _pair("Consignee", "413 (I) Engr Sqn", 100, 250, y=360),
             _pair("Unit", "QBIDENGR SAN", 100, 220, y=900)]
    got = match_fields(_fields("unit_from", "unit_to", "from_depot", "consignor", "consignee"), pairs, W)
    for n in ("unit_from", "from_depot", "consignor"):
        assert got[n].pair.value == "Comdt ESD Delhi Cantt", n
    assert got["consignee"].pair.value == "413 (I) Engr Sqn"
    assert "unit_to" not in got          # a left-half value is never a right-column unit: blank, not wrong


def test_a_pair_is_not_shared_between_unrelated_fields():
    got = match_fields(_fields("date", "rv_date"), [_pair("Date", "12 Jan 2026", 100, 160)], W)
    assert len(got) == 1


def test_zone_ok_rules():
    f = _fields("unit_from")[0]
    assert zone_ok(f, ["unit"], 0.2, 0.25) and not zone_ok(f, ["unit"], 0.7, 0.75)
    assert not zone_ok(f, ["issued", "to"], 0.2, 0.6)           # value in the other half
    assert zone_ok({"name": "x"}, ["unit"], 0.9, 0.9)           # no side: never restricted


def _page(lines, width=1000):
    return PageData(page_number=1, width=width, height=1400, scale=1.0, source="ocr", lines=lines)


def _line(parts, y):
    words = [OCRWord(text=t, bbox=BoundingBox(x1=x, y1=y, x2=x + w, y2=y + 30), confidence=0.9) for t, x, w in parts]
    return OCRLine(text=" ".join(w.text for w in words), words=words,
                   bbox=BoundingBox(x1=words[0].bbox.x1, y1=y, x2=words[-1].bbox.x2, y2=y + 30), confidence=0.9)


def test_proximity_does_not_fill_a_hidden_left_unit_with_the_right_columns_text():
    bus = EventBus("j", redis_conn=None)
    bus._redis = None
    ex = FieldExtractor([{"name": "unit_from"}], bus)
    # "Unit" (left label) then, far to the right, "VMSD Chheoki," -- the left value is under a stamp
    ex.pages[1] = _page([_line([("Unit", 170, 70), ("VMSD", 560, 90), ("Chheoki,", 660, 130)], 160)])
    hit = ex._proximity(ex.fields[0])
    assert hit is not None and hit[2] == []                     # label found, no value taken from the other column


def test_page_types():
    voucher = "ISSUE RECEIPT AND EXPENCE VOUCHER IV No Unit Stn c/o 56 APO Dated Issued to Back loading Auth Veh BA No Type of Veh Engine No Chassis No Date of Final Insp Vintage Veh Log Book"
    assert classify_page(voucher)[0] == "tms_backload_voucher"
    assert classify_page("OBD Creation date STR No Tracking Number STR Date STO No Priority Type of STR Authority SUS No")[0] == "mms_collection_rv"
    assert classify_page("the quick brown fox jumps over the lazy dog")[0] == ""


def test_header_repair_and_records():
    heads = ["S$ No", "PersNo Rank & Name", "DOC’))DOS", "Column 4", "DOR (Capt)", "DOB", "/Unit & SUS No", "Arms", "Gender", "TOS"]
    known = normalize_headers(heads)
    assert [k["text"] for k in known][:5] == ["S No", "PersNo Rank & Name", "DOC", "DOS", "DOR"]
    assert [k["id"] for k in known][2:4] == ["doc", "dos"]
    assert normalize_headers(["Ser", "Foo bar"])[0]["text"] == "Ser" and normalize_headers(["Ser", "Foo bar"])[1]["id"] == ""
    rec = table_records(known, [["21", "MS-22214W Capt Harsh Kumar", "02-12-2024", "O2- 12 2024", "02-122029", ")08 12- 1998",
                                 "303FdHosp&3755093P", "AMC", "male", "19-09-2025"]])[0]
    assert rec["pers_no"] == "MS-22214W" and rec["rank"] == "Capt" and rec["name"] == "Harsh Kumar"
    assert rec["unit"] == "303 Fd Hosp" and rec["sus_no"] == "3755093P" and rec["gender"] == "Male"
    assert rec["dos"] == "02-12-2024" and rec["dor"] == "02-12-2029" and rec["dob"] == "08-12-1998"
    assert table_records(normalize_headers(["Foo", "Bar"]), [["a", "b"]]) == []


def test_date_text_leaves_nonsense_alone():
    assert _date_text("nonsense") == "nonsense" and _date_text("31-02-2024") == "31-02-2024"


def test_mms_tms_table_headers_are_known():
    from app.knowledge.kb import canonical_header
    for printed, label in [("Serial No", "S No"), ("Material Description", "Nomenclature"), ("AIU", "AU"),
                           ("Demand Qty", "Demand Qty"), ("UH Qty", "UH Qty"), ("Regn Nos", "Regd Nos"),
                           ("Veh BA", "Veh BA No"), ("Section Number", "Section Number")]:
        got = canonical_header(printed)
        assert got and got["label"] == label, printed


def test_a_header_fragment_is_not_a_date_or_number_value():
    from app.ocr.kv_match import _value_ok
    f = {f["name"]: f for f in enrich_fields([{"name": "dos"}, {"name": "pers_no"}, {"name": "unit"}])}
    assert not _value_ok(f["dos"], "el") and not _value_ok(f["dos"], "3°")
    assert _value_ok(f["dos"], "02-12-2024") and _value_ok(f["dos"], "Jul 2025")
    assert not _value_ok(f["pers_no"], "Name") and _value_ok(f["pers_no"], "MS-22214W")
    assert _value_ok(f["unit"], "303 Fd Hosp")


def test_rank_misread_by_one_letter_is_snapped_to_the_closed_list():
    from app.knowledge.kb import _split_pers_rank_name
    assert _split_pers_rank_name("MS-22214W Cant Harshit Shah") == {"pers_no": "MS-22214W", "rank": "Capt", "name": "Harshit Shah"}
    assert _split_pers_rank_name("MS-22214W Capt Harshit Shah")["rank"] == "Capt"
    assert _split_pers_rank_name("MS-22214W Harshit Shah")["rank"] == ""          # no rank printed: nothing invented
    assert _split_pers_rank_name("MS-22214W Lx Harshit")["rank"] == ""            # short ranks are never guessed


def test_copy_vote_prefers_the_valid_reading_then_the_much_more_confident_one():
    from app.fast.copyvote import pick_best_copy
    f = {f["name"]: f for f in enrich_fields([{"name": "date"}, {"name": "unit_from"}])}
    bad = {"value": "QZ Jun 2025", "confidence": 0.9}
    good = {"value": "02 Jun 2025", "confidence": 0.5}
    assert pick_best_copy(f["date"], bad, [good]) == 1                             # parses beats confident-but-broken
    assert pick_best_copy(f["date"], good, [bad]) == 0
    a = {"value": "2 KUMAON", "confidence": 0.40}
    b = {"value": "2 KUMAON", "confidence": 0.70}
    assert pick_best_copy(f["unit_from"], a, [b]) == 1                             # equally valid: clearly better wins
    assert pick_best_copy(f["unit_from"], b, [a]) == 0
    c = {"value": "2 KUMAON", "confidence": 0.45}
    assert pick_best_copy(f["unit_from"], a, [c]) == 0                             # a small gap is not enough


def test_copy_vote_ignores_a_different_voucher():
    from app.fast.copyvote import pick_best_copy
    f = enrich_fields([{"name": "date"}])[0]
    assert pick_best_copy(f, {"value": "QZ Jun 2025", "confidence": 0.9}, [{"value": "15 Nov 2024", "confidence": 0.9}]) == 0


def test_cue_pages_are_recognised_and_their_fields_are_known():
    ty, score, _ = classify_page("RESTRICTED Appendix 'A' This supersedes Govt letter WE No : 3062/2015/1 Effective From : 01 Jun 2015 "
                                 "WAR ESTABLISHMENT FOR STANDARD INFANTRY BATTALION Officers : 20 JCOs : 42 ORs 793")
    assert ty == "cue_war_establishment"
    ty, _, _ = classify_page("ADOPTION OF REVISED WE 3062/2015/1 Integrated Headquarters of MoD (Army) General Staff Branch Dte Gen of Infantry Encls : As above")
    assert ty == "cue_adoption_letter"
    f = {x["name"]: x for x in enrich_fields([{"name": "we_no"}, {"name": "jcos"}])}
    assert "War Establishment No" in f["we_no"]["aliases"] and f["jcos"].get("exact_labels")
