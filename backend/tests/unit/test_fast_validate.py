from app.fast.validate import assess, parse_date
from app.fast.vlm import supported_by_ocr


def test_parse_date_formats():
    assert str(parse_date("12/06/2026")) == "2026-06-12"
    assert str(parse_date("12 Jun 2026")) == "2026-06-12"
    assert str(parse_date("Jun 12, 2026")) == "2026-06-12"
    assert str(parse_date("2026-06-12")) == "2026-06-12"
    assert parse_date("31/02/2026") is None
    assert parse_date("hello") is None


def test_validated_needs_checks_and_confidence():
    f = {"name": "rv_date"}
    assert assess(f, "12 Jun 2026", 0.95).status == "validated"
    bad = assess(f, "12 Jxn 2O26", 0.95)
    assert bad.status == "review_required" and "date" in bad.reason
    low = assess({"name": "unit"}, "9 ASSAM", 0.40)
    assert low.status == "review_required" and "low_confidence" in low.reason
    assert assess({"name": "unit"}, "", 0.99).status == "review_required"


def test_pattern_and_number_checks():
    assert assess({"name": "ref", "pattern": r"^IV/\d+$"}, "IV/2041", 0.9).status == "validated"
    assert assess({"name": "ref", "pattern": r"^IV/\d+$"}, "IV/20x1", 0.9).status == "review_required"
    assert assess({"name": "qty", "type": "number"}, "1,250.50", 0.9).status == "validated"
    assert assess({"name": "qty", "type": "number"}, "abc", 0.9).status == "review_required"


def test_vlm_answer_must_be_backed_by_ocr_text():
    assert supported_by_ocr("IV/2041", "IV No : IV/2041 Unit") is True
    assert supported_by_ocr("Rajesh Kumar", "Name Rajesh Kumer") is True      # one wrong character allowed
    assert supported_by_ocr("INV-9999", "IV No : IV/2041") is False             # invented
    assert supported_by_ocr("anything", "") is False
    assert assess({"name": "x"}, "INV-9999", 0.99, supported=False).status == "review_required"
