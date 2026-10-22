import os
import time

from app.fast.ocr_cache import OcrCache
from app.ocr.base import BoundingBox, OCRLine, OCRWord


def _line(t="hello"):
    return OCRLine(text=t, words=[OCRWord(text=t, bbox=BoundingBox(x1=1, y1=2, x2=3, y2=4), confidence=0.9)],
                   bbox=BoundingBox(x1=1, y1=2, x2=3, y2=4), confidence=0.9)


def test_roundtrip_and_key_changes(tmp_path):
    c = OcrCache(tmp_path, True)
    k = OcrCache.make_key("sha", 1, 3.0, "tesseract", "5.3.0")
    assert c.get(k) is None
    c.put(k, [_line()])
    got = c.get(k)
    assert got and got[0].text == "hello" and got[0].words[0].bbox.x2 == 3
    # any change of file, page, scale, engine or version is a different key
    for other in (("sha2", 1, 3.0, "tesseract", "5.3.0"), ("sha", 2, 3.0, "tesseract", "5.3.0"),
                  ("sha", 1, 2.0, "tesseract", "5.3.0"), ("sha", 1, 3.0, "tesseract", "5.4.0")):
        assert OcrCache.make_key(*other) != k


def test_empty_result_is_never_cached(tmp_path):
    c = OcrCache(tmp_path, True)
    k = OcrCache.make_key("s", 1, 1.0, "tesseract", "v")
    c.put(k, [])
    assert c.get(k) is None


def test_disabled_cache_is_a_noop(tmp_path):
    c = OcrCache(tmp_path, False)
    k = OcrCache.make_key("s", 1, 1.0, "t", "v")
    c.put(k, [_line()])
    assert c.get(k) is None


def test_prune_by_size_and_age(tmp_path):
    c = OcrCache(tmp_path, True, max_mb=1, max_age_days=1)
    keys = [OcrCache.make_key("s", i, 1.0, "t", "v") for i in range(3)]
    for k in keys:
        c.put(k, [_line("x" * 400_000)])      # ~0.4 MB each
        time.sleep(0.01)
    old = c._path(keys[0])
    os.utime(old, (time.time() - 3 * 86400,) * 2)   # past the age limit
    c.prune()
    assert c.get(keys[0]) is None
    assert c.get(keys[2]) is not None


def test_tsv_parser_survives_quote_characters():
    from app.ocr.tesseract import TesseractOCREngine
    head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    rows = (
        '5\t1\t1\t1\t1\t1\t10\t10\t30\t10\t90\t"Issued\n'      # unbalanced quote
        "5\t1\t1\t1\t1\t2\t50\t10\t30\t10\t90\tto\n"
        "5\t1\t1\t1\t2\t1\t10\t30\t30\t10\t90\tNext\n"
    )
    lines = TesseractOCREngine._parse_tsv_to_lines(head + rows)
    assert [l.text for l in lines] == ['"Issued to', "Next"]
