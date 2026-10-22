from app.fast.triage import DocumentSource, text_layer_ok, words_to_lines
from tests.unit._fast_helpers import make_digital_pdf, make_scan_like_pdf


def test_digital_page_uses_text_layer_with_pixel_boxes(tmp_path):
    src = DocumentSource(make_digital_pdf(str(tmp_path / "d.pdf")), "application/pdf")
    img, lines, scale = src.load_page(0)
    assert lines is not None and scale > 1
    texts = [ln.text for ln in lines]
    assert "Name : Rajesh Kumar" in texts
    first = lines[0].words[0].bbox
    assert 0 < first.x1 < img.width and 0 < first.y1 < img.height  # coordinates are image pixels
    assert first.x1 == __import__("pytest").approx(50 * scale, abs=6)


def test_picture_page_with_watermark_text_is_not_digital(tmp_path):
    src = DocumentSource(make_scan_like_pdf(str(tmp_path / "s.pdf")), "application/pdf")
    _, lines, _ = src.load_page(0)
    assert lines is None          # goes to OCR; the watermark words are not the page content


def test_text_layer_ok_rejects_garbage():
    assert not text_layer_ok("", 0)
    assert not text_layer_ok("(cid:12)(cid:13) " * 20, 40)
    assert not text_layer_ok("�" * 80, 20)
    assert text_layer_ok("This is a perfectly readable sentence with enough words to count as text.", 14)


def test_words_to_lines_groups_by_position_not_drawing_order():
    # label and value drawn as separate blocks, same visual row
    words = [(200, 10, 260, 22, "John"), (10, 10, 60, 22, "Name"), (70, 10, 80, 22, ":"), (10, 40, 60, 52, "Next")]
    lines = words_to_lines(words)
    assert [ln.text for ln in lines] == ["Name : John", "Next"]
