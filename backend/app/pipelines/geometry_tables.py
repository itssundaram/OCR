"""
One table reader for every pipeline.

fast_ocr reads tables from the page's own geometry (ruling lines, the gaps between words, the text lines) and then
understands them (header / title / footer, multi-page join, column kinds, per-cell checks). That reader is the best
one we have, so the other pipelines use it too and keep their own model only as a fallback:

    geometry_first_tables(pages, job_id, fallback)
        1. every page is read once (words from one OCR pass, cached by pixel hash) and its tables are found by
           app.fast.tables / app.fast.tableschema - the same code path fast_ocr runs;
        2. pages on which that found NO table are handed to `fallback(page_indexes)` - the pipeline's own table
           model (TATR / img2table) - exactly as before;
        3. the two lists are merged in page order and numbered again.

Nothing here knows a document type, a column name or a vocabulary. Switch off with TABLE_GEOMETRY_FIRST=false.
"""
from __future__ import annotations

from typing import Callable

from PIL import Image

from app.core.config import settings
from app.core.logging import get_logger
from app.pipelines.base import TableResult

logger = get_logger(__name__)


def _pages_of(t: TableResult) -> set[int]:
    pages = (t.meta or {}).get("pages")
    return {int(p) for p in pages} if pages else {t.page_number}


def read_tables_by_geometry(pages: list[Image.Image], template_fields: list[dict] | None = None) -> list[TableResult]:
    """Tables of all pages by geometry. A page that cannot be read is skipped (logged), never fatal."""
    from app.fast.ocr_cache import default_cache
    from app.fast.reader import PageReader
    from app.fast.tables import extract_page_tables
    from app.fast.tableschema import finalize_tables

    reader = PageReader(None, default_cache())
    page_data: dict = {}
    raw: list[TableResult] = []
    for i, img in enumerate(pages, start=1):
        try:
            pd = reader.from_image(img, i)
            page_data[i] = pd
            raw.extend(extract_page_tables(pd, first_index=len(raw) + 1))
        except Exception as exc:
            logger.warning("geometry_tables_page_failed", page=i, error=str(exc)[:200])
    if not raw:
        return []
    try:
        out = finalize_tables(raw, page_data, template_fields)
    except Exception as exc:                          # understanding is an upgrade; the raw grids still ship
        logger.warning("geometry_tables_finalize_failed", error=str(exc)[:200])
        out = raw
    return out


def geometry_first_tables(
    pages: list[Image.Image],
    job_id: str,
    fallback: Callable[[list[int]], list[TableResult]],
    template_fields: list[dict] | None = None,
) -> list[TableResult]:
    """Geometry reader first; `fallback(page_indexes)` (0-based) only for pages it found no table on."""
    all_pages = list(range(len(pages)))
    if not settings.TABLE_GEOMETRY_FIRST or not pages:
        return _renumber(fallback(all_pages) if pages else [])
    try:
        geo = read_tables_by_geometry(pages, template_fields)
    except Exception as exc:
        logger.warning("geometry_tables_failed", job_id=job_id, error=str(exc)[:200])
        geo = []
    covered: set[int] = set()
    for t in geo:
        covered |= _pages_of(t)
    missing = [i for i in all_pages if (i + 1) not in covered]
    model: list[TableResult] = []
    if missing:
        try:
            model = fallback(missing)
        except Exception as exc:
            logger.warning("table_model_fallback_failed", job_id=job_id, error=str(exc)[:200])
    logger.info("tables_geometry_first", job_id=job_id, geometry=len(geo), fallback=len(model), fallback_pages=len(missing))
    return _renumber(geo + model)


def _renumber(tables: list[TableResult]) -> list[TableResult]:
    tables = sorted(tables, key=lambda t: (t.page_number, t.table_index))
    for i, t in enumerate(tables, start=1):
        t.table_index = i
    return tables
