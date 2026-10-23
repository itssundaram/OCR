"""
FastEngine: the orchestration that both entry points share.

  run_pages()        pages are read in parallel; as each one lands its key:value
                     pairs are scanned, provisional fields are announced and its
                     table job is started (tables never block fields)
  finalize_fields()  final matching, label proximity, VLM repair, validation
  collect_tables()   waits for the table jobs, each with its own time limit
"""
from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.fast.events import EventBus
from app.fast.fields import FieldExtractor
from app.fast.structure import build_blocks
from app.fast.tables import extract_page_tables
from app.fast.tableschema import finalize_tables
from app.fast.types import PageData
from app.pipelines.base import FieldResult, TableResult

logger = get_logger(__name__)


class JobCancelled(Exception):
    pass


class FastEngine:
    def __init__(self, template_fields: list[dict], bus: EventBus, want_tables: bool = True, anchors: dict | None = None):
        self.bus = bus
        self.want_tables = want_tables
        self.extractor = FieldExtractor(template_fields, bus, anchors=anchors)
        self.pages: dict[int, PageData] = {}
        self.blocks: dict[int, list[dict]] = {}
        self._table_futs: dict[int, tuple[Future, float]] = {}
        self._table_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fast-tbl")
        self.timings: dict[str, int] = {}
        self.warnings: list[str] = []
        self._t_start = time.perf_counter()
        self._first_field_ms: int | None = None

    # ── reading ────────────────────────────────────────────────────────────────
    def run_pages(
        self,
        n_pages: int,
        load_page: Callable[[int], PageData],
        on_page: Callable[[PageData], None] | None = None,
    ) -> None:
        t0 = time.perf_counter()
        done = 0
        threads = max(1, min(settings.FAST_PAGE_THREADS, n_pages))

        def _load(i: int) -> PageData:
            if self.bus.cancel_requested():
                raise JobCancelled()
            self.bus.emit("page_started", page=i + 1)
            return load_page(i)

        with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="fast-page") as pool:
            futs = {pool.submit(_load, i): i for i in range(n_pages)}
            for fut in as_completed(futs):
                page = fut.result()            # a failed page read fails the job
                self.pages[page.page_number] = page
                if on_page:
                    on_page(page)
                before = len(self.extractor._announced)
                self.extractor.add_page(page)
                if self._first_field_ms is None and len(self.extractor._announced) > before:
                    self._first_field_ms = int((time.perf_counter() - self._t_start) * 1000)
                self.blocks[page.page_number] = build_blocks(page)
                for w in page.warnings:
                    self.warnings.append(f"page {page.page_number}: {w}")
                if self.want_tables:
                    self._table_futs[page.page_number] = (self._table_pool.submit(extract_page_tables, page), time.monotonic())
                done += 1
                self.bus.emit(
                    "page_completed", page=page.page_number, pages_done=done, pages_total=n_pages,
                    source=page.source, lines=len(page.lines), ms=page.ms.get("total", 0),
                )
        self.timings["read_pages_ms"] = int((time.perf_counter() - t0) * 1000)

    # ── fields ─────────────────────────────────────────────────────────────────────
    def finalize_fields(self) -> list[FieldResult]:
        t0 = time.perf_counter()
        fields = self.extractor.finalize()
        if self.bus.cancel_requested():
            raise JobCancelled()
        self.timings["fields_ms"] = int((time.perf_counter() - t0) * 1000)
        self.timings.update(self.extractor.timings)
        if self._first_field_ms is not None:
            self.timings["first_field_ms"] = self._first_field_ms
        return fields

    # ── tables ──────────────────────────────────────────────────────────────────────
    def collect_tables(self) -> list[TableResult]:
        t0 = time.perf_counter()
        out: list[TableResult] = []
        limit = float(settings.FAST_TABLE_TIMEOUT_S)
        for page_no in sorted(self._table_futs):
            fut, submitted = self._table_futs[page_no]
            remaining = max(0.1, limit - (time.monotonic() - submitted))
            try:
                found = fut.result(timeout=remaining)
            except Exception as exc:  # timeout or table error: no table, job goes on
                self.warnings.append(f"page {page_no}: tables skipped ({type(exc).__name__})")
                logger.warning("fast_tables_skipped", page=page_no, error=str(exc) or type(exc).__name__)
                continue
            out.extend(found)
        self._table_pool.shutdown(wait=False, cancel_futures=True)
        try:
            out = finalize_tables(out, self.pages, self.extractor.fields)
        except Exception as exc:                  # understanding is an upgrade; raw grids still ship
            logger.warning("fast_table_finalize_failed", error=str(exc), exc_info=True)
            self.warnings.append("table analysis skipped")
        for i, t in enumerate(out, start=1):
            t.table_index = i
            self.bus.emit(
                "table_extracted", page=t.page_number, table_index=t.table_index, headers=t.headers,
                rows=t.rows, bbox=t.bbox, method=t.extraction_method, confidence=t.confidence,
                quality=(t.meta or {}).get("quality"), pages=(t.meta or {}).get("pages"),
            )
        self.timings["tables_wait_ms"] = int((time.perf_counter() - t0) * 1000)
        return out

    # ── helpers for result building ───────────────────────────────────────────────
    def sorted_pages(self) -> list[PageData]:
        return [self.pages[k] for k in sorted(self.pages)]

    def all_detections(self, cap: int = 6000) -> list[dict]:
        dets: list[dict] = []
        for page in self.sorted_pages():
            for i, w in enumerate(page.words()):
                if w.bbox is None:
                    continue
                dets.append({
                    "id": f"det_p{page.page_number}_{i}", "text": w.text,
                    "bbox": {"x1": w.bbox.x1, "y1": w.bbox.y1, "x2": w.bbox.x2, "y2": w.bbox.y2},
                    "confidence": round(w.confidence or 0.0, 3), "page_number": page.page_number,
                })
                if len(dets) >= cap:
                    return dets
        return dets

    def overall_confidence(self, fields: list[FieldResult]) -> float:
        vals = [f.confidence for f in fields if f.source != "discovered" and (f.value or "").strip()]
        return round(sum(vals) / len(vals), 4) if vals else 0.0
