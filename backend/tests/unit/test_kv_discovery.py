"""Unit tests for app/ocr/kv_discovery.py — hand-built OCR geometry, no OCR engine."""
from app.ocr.base import BoundingBox, OCRLine, OCRWord
from app.ocr.kv_discovery import assign_unique_keys, discover_key_values, slugify_label


def _line(y, *words, h=20, conf=0.95):
    """words: (text, x) pairs; each word is len(text)*11 px wide."""
    ocr_words = [
        OCRWord(text=t, bbox=BoundingBox(x1=x, y1=y, x2=x + len(t) * 11, y2=y + h), confidence=conf)
        for t, x in words
    ]
    return OCRLine(
        text=" ".join(t for t, _ in words),
        words=ocr_words,
        bbox=BoundingBox(
            x1=min(w.bbox.x1 for w in ocr_words), y1=y,
            x2=max(w.bbox.x2 for w in ocr_words), y2=y + h,
        ),
        confidence=conf,
    )


def _pairs(*lines):
    return {p.label: p for p in discover_key_values([list(lines)])}


def test_simple_label_value():
    got = _pairs(_line(100, ("Tele:", 100), ("2309", 170), ("4765", 225)))
    assert got["Tele"].value == "2309 4765"
    assert got["Tele"].page_number == 1
    assert got["Tele"].bbox["x1"] == 170  # box covers the value only, not the label


def test_two_pairs_on_one_line():
    got = _pairs(_line(100, ("Phone:", 100), ("011-2301", 180), ("Fax:", 330), ("011-9000", 380)))
    assert got["Phone"].value == "011-2301"
    assert got["Fax"].value == "011-9000"


def test_multi_word_label():
    got = _pairs(_line(100, ("Date", 100), ("of", 150), ("Birth:", 180), ("05-06-88", 250)))
    assert got["Date of Birth"].value == "05-06-88"


def test_two_columns_in_one_ocr_line_are_not_merged():
    # Tesseract can join a left and a right column into a single line.
    got = _pairs(_line(100, ("Tele:", 100), ("2309", 170), ("Dte", 900), ("Gen", 950), ("Services", 1000)))
    assert got["Tele"].value == "2309"  # must not swallow the right column


def test_time_and_urls_are_not_pairs():
    assert _pairs(_line(100, ("Time", 100), ("10:30", 160), ("hrs", 220))) == {}
    assert _pairs(_line(100, ("See", 100), ("http://example.com", 150))) == {}


def test_value_on_next_line_when_label_is_alone():
    got = _pairs(_line(100, ("Name:", 100)), _line(130, ("Capt", 100), ("Pandey", 160)))
    assert got["Name"].value == "Capt Pandey"


def test_value_to_the_right_when_ocr_split_the_row():
    got = _pairs(_line(100, ("Subject:", 100)), _line(101, ("Allotment", 400), ("of", 500), ("numbers", 540)))
    assert got["Subject"].value == "Allotment of numbers"


def test_label_with_no_value_is_dropped():
    assert _pairs(_line(100, ("Remarks:", 100))) == {}


def test_inline_colon_without_space():
    got = _pairs(_line(100, ("Ref:00961/DGDS-1", 100)))
    assert got["Ref"].value == "00961/DGDS-1"


def test_split_colon_token():
    got = _pairs(_line(100, ("Tele", 100), (":", 150), ("2309", 175)))
    assert got["Tele"].value == "2309"


def test_confidence_is_mean_of_value_words():
    line = _line(100, ("Ref:", 100), ("A1", 160), ("B2", 200))
    line.words[1].confidence = 0.9
    line.words[2].confidence = 0.5
    assert abs(_pairs(line)["Ref"].confidence - 0.7) < 1e-6


def test_page_numbers_follow_input_order():
    pairs = discover_key_values([[_line(100, ("Ref:", 100), ("x1", 150))], [_line(100, ("Doc:", 100), ("y2", 150))]])
    assert [(p.label, p.page_number) for p in pairs] == [("Ref", 1), ("Doc", 2)]


def test_unique_keys_never_collide_with_template_fields():
    pairs = discover_key_values([[
        _line(100, ("Ref:", 100), ("a1", 150)),
        _line(200, ("Ref:", 100), ("b2", 150)),
    ]])
    keys = [k for k, _ in assign_unique_keys(pairs, taken={"ref"})]
    assert keys == ["ref_2", "ref_3"]


def test_slugify():
    assert slugify_label("Date of Birth") == "date_of_birth"
    assert slugify_label("S.No.") == "s_no"


def test_wide_gap_before_colon_stays_with_label():
    # typed forms: "OBD Number            : 0082830253"
    got = _pairs(_line(10, ("OBD", 10), ("Number", 50), (":", 400), ("0082830253", 430)))
    assert got["OBD Number"].value == "0082830253"


def test_misread_colon_symbols():
    got = _pairs(_line(10, ("From", 10), (">", 300), ("JOD", 330), ("PROV", 380)))
    assert got["From"].value == "JOD PROV"


def test_colon_glued_to_value():
    got = _pairs(_line(10, ("Voucher", 10), ("No", 90), (":IV/CS/03", 300)))
    assert got["Voucher No"].value == "IV/CS/03"


def test_sentence_end_is_not_a_label():
    got = _pairs(_line(10, ("initial", 10), ("commissioning:", 80), ("1.", 250), ("Name", 290)))
    assert "commissioning" not in got


# ── pairs printed without a colon ─────────────────────────────────────────────────────────────────────────────
KNOWN = frozenset({"unit", "station", "kms", "depot", "date"})


def _bare(*lines, known=KNOWN):
    return [p for p in discover_key_values([list(lines)], known) if p.kind == "bare"]


def test_bare_pair_known_label_prefix():
    got = _bare(_line(100, ("Unit", 100), ("14", 160), ("JAK", 190), ("RIF", 230)))
    assert [(p.label, p.value) for p in got] == [("Unit", "14 JAK RIF")]
    assert got[0].bbox["x1"] == 160                      # the box covers the value only


def test_bare_pair_label_and_value_split_by_wide_gap():
    got = _bare(_line(100, ("Station", 100), ("c/o", 420), ("99", 460), ("APO", 500)))
    assert [(p.label, p.value) for p in got] == [("Station", "c/o 99 APO")]


def test_bare_pairs_need_the_known_vocabulary():
    line = _line(100, ("Unit", 100), ("14", 160), ("JAK", 190), ("RIF", 230))
    assert discover_key_values([[line]]) == []           # without known labels only "Label: value" is found
    assert _bare(line, known=frozenset()) == []


def test_bare_pair_rejects_sentence_and_next_label():
    assert _bare(_line(100, ("Unit", 100), ("will", 160), ("be", 210), ("shown", 245))) == []
    assert _bare(_line(100, ("Unit", 100), ("Station", 160))) == []


def test_bare_pair_colon_pair_wins():
    got = discover_key_values([[_line(100, ("Unit:", 100), ("9", 170), ("ASSAM", 200))]], KNOWN)
    assert [(p.label, p.value, p.kind) for p in got] == [("Unit", "9 ASSAM", "colon")]


def test_bare_pairs_stacked_in_a_table_column_are_dropped():
    rows = [_line(100 + 40 * i, ("Depot", 100), ("2", 420)) for i in range(3)]
    assert _bare(*rows) == []                            # a table column, not a form field
    assert len(_bare(*rows[:1])) == 1                    # the same pair once is a real field
