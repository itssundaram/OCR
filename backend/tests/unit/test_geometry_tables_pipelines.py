"""Every pipeline reads tables with the shared geometry reader first and keeps its own model as a fallback."""
import fitz
import pytest
from PIL import Image

from app.core.config import settings
from app.pipelines.base import TableResult
from app.pipelines.geometry_tables import geometry_first_tables
from app.pipelines.registry import get_pipeline
from tests.unit.test_table_gridless import ROWS, _invoice

PIPELINES = ["fast_ocr", "surya", "tesseract", "learned_anchor"]


def _pages(path: str, n_blank: int = 0) -> list[Image.Image]:
    pix = fitz.open(path)[0].get_pixmap(dpi=200)
    pages = [Image.frombytes("RGB", (pix.width, pix.height), pix.samples)]
    pages += [Image.new("RGB", pages[0].size, "white") for _ in range(n_blank)]
    return pages


@pytest.fixture()
def invoice_pages(tmp_path):
    return _pages(_invoice(str(tmp_path / "i.pdf"), ROWS), n_blank=1)


@pytest.mark.parametrize("name", ["surya", "tesseract", "learned_anchor"])
def test_pipeline_reads_a_gridless_table_by_geometry_and_calls_its_model_only_for_the_blank_page(name, invoice_pages, monkeypatch):
    pipe = get_pipeline(name)
    asked = []

    def model(pages, indexes):
        asked.append(list(indexes))
        return [TableResult(table_index=1, page_number=2, headers=["x", "y"], rows=[["1", "2"]], extraction_method="model")]

    monkeypatch.setattr(pipe, "_model_tables", model)
    tables = pipe.extract_tables(invoice_pages, "job-geo")
    assert asked == [[1]]                                   # page 2 (index 1) had no geometry table; page 1 never reached the model
    assert [t.extraction_method for t in tables] == ["grid_lines", "model"]
    assert [t.table_index for t in tables] == [1, 2]
    t = tables[0]
    assert t.headers[1:] == ["Item Description", "Status", "Qty", "Rate", "Amount"] and len(t.rows) == 7   # OCR may misread "Sl No"
    assert t.cells and all(c["bbox"] for c in t.cells)      # cells carry geometry for the review screen


@pytest.mark.parametrize("name", ["surya", "tesseract", "learned_anchor"])
def test_switching_the_geometry_reader_off_gives_the_models_every_page(name, invoice_pages, monkeypatch):
    pipe = get_pipeline(name)
    monkeypatch.setattr(settings, "TABLE_GEOMETRY_FIRST", False)
    asked = []
    monkeypatch.setattr(pipe, "_model_tables", lambda pages, idx: asked.append(list(idx)) or [])
    pipe.extract_tables(invoice_pages, "job-off")
    assert asked == [[0, 1]]


def test_a_failing_model_fallback_never_loses_the_geometry_tables(invoice_pages):
    def boom(idx):
        raise RuntimeError("no weights")

    tables = geometry_first_tables(invoice_pages, "job-x", boom)
    assert len(tables) == 1 and tables[0].extraction_method == "grid_lines"


def test_every_registered_pipeline_has_a_table_reader():
    for name in PIPELINES:
        assert callable(getattr(get_pipeline(name), "extract_tables"))
