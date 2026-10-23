"""
Background refinement of a finished extraction (draft -> better).

The job is COMPLETED and visible as soon as the first pass is saved. This task
then re-reads the cells that were flagged, most useful first:

  1. what the viewer is looking at (focus), 2. cheap re-reads before model
  calls, 3. reading order.

It works in short slices (FAST_REFINE_SLICE_S): do a batch, save it, announce it
(`cell_refined` on the job's event stream), and when time is up put itself back
on the queue so new uploads are never stuck behind a long refinement.

Safety rules
  * a cell a person edited is never overwritten (per-cell `edited` + `rev`)
  * a new reading is accepted only if it passes the column's checks
  * saving re-reads the stored result first and merges, so user edits made
    while a batch was being computed survive
  * cancel (re-run / explicit) stops it between cells
"""
from __future__ import annotations

import json
import time
from copy import deepcopy

from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import SessionLocal
from app.db.models import Document, ProcessingJob, ProcessingResult
from app.fast.events import EventBus
from app.fast.refine_cells import (
    Change, CropReader, apply_changes, mark_tried, pending_targets, refine_with_tesseract, refine_with_vlm,
)

logger = get_logger(__name__)

TASK_PATH = "app.fast.refine.run_refine_slice"


def focus_key(job_id: str) -> str:
    return f"docint:job:{job_id}:focus"


def refine_cancel_key(job_id: str) -> str:
    return f"docint:job:{job_id}:refine_cancel"


def _redis():
    try:
        from app.workers.queue import redis_conn
        return redis_conn
    except Exception:
        return None


# ── small API used by the web routes ─────────────────────────────────────────
def set_focus(job_id: str, focus: dict) -> bool:
    r = _redis()
    if r is None:
        return False
    try:
        r.set(focus_key(job_id), json.dumps(focus), ex=600)
        return True
    except Exception:
        return False


def get_focus(job_id: str) -> dict | None:
    r = _redis()
    if r is None:
        return None
    try:
        raw = r.get(focus_key(job_id))
        return json.loads(raw) if raw else None
    except Exception:
        return None


def cancel_refine(job_id: str) -> bool:
    r = _redis()
    if r is None:
        return False
    try:
        r.set(refine_cancel_key(job_id), "1", ex=3600)
        return True
    except Exception:
        return False


def _cancelled(job_id: str) -> bool:
    r = _redis()
    try:
        return bool(r is not None and r.exists(refine_cancel_key(job_id)))
    except Exception:
        return False


# ── queueing ─────────────────────────────────────────────────────────────────
def dispatch_refine(job_id: str, part: int = 1) -> str | None:
    """Queue (or re-queue) refinement. Prefers a worker on the refine queue, then
    the fast queue, then the default queue. None = nothing can serve it."""
    conn = _redis()
    if conn is None:
        return None
    from app.fast.dispatch import _worker_listening
    try:
        from rq import Queue
        for name in (settings.FAST_REFINE_QUEUE_NAME, settings.FAST_QUEUE_NAME, settings.REDIS_QUEUE_NAME):
            if _worker_listening(conn, name):
                Queue(name, connection=conn).enqueue(
                    TASK_PATH, job_id, job_id=f"refine:{job_id}:{part}", job_timeout=settings.FAST_REFINE_SLICE_S * 6 + 120)
                return name
    except Exception as exc:
        logger.warning("refine_enqueue_failed", job_id=job_id, error=str(exc))
    return None


def initial_refine_state(tables: list[dict]) -> dict | None:
    """Meta stored with the first result: how many cells will be looked at again."""
    if not settings.FAST_REFINE_ENABLED:
        return None
    flagged = sum(1 for t in tables for c in t.get("cells") or [] if c.get("state") == "flagged" and c.get("bbox"))
    if not flagged:
        return None
    return {"status": "pending", "flagged_at_start": flagged, "improved": 0, "vlm_calls": 0, "slices": 0}


# ── the task ─────────────────────────────────────────────────────────────────
class _Pages:
    def __init__(self, doc: Document):
        from app.fast.triage import DocumentSource
        self.source = DocumentSource(doc.file_path, doc.file_type)
        self._cache: dict[int, object] = {}

    def get(self, page_no: int):
        if page_no not in self._cache:
            if len(self._cache) >= 3:
                self._cache.pop(next(iter(self._cache)))
            self._cache[page_no] = self.source.load_page(page_no - 1)[0]
        return self._cache[page_no]


def _remaining(tables: list[dict]) -> int:
    return sum(1 for t in tables for c in t.get("cells") or [] if c.get("state") == "flagged")


def _persist(job_id: str, changes: list[Change], tried: list, refine_patch: dict) -> list[Change]:
    """Merge a batch into the STORED result (fresh read, user edits win)."""
    from app.fast.refine_cells import mark_tried as _mark
    with SessionLocal() as db:
        row = db.query(ProcessingResult).filter_by(job_id=job_id).with_for_update().first()
        if row is None:
            return []
        ej = dict(row.extracted_json or {})
        stored = deepcopy(ej.get("tables") or [])
        applied = apply_changes(stored, changes)
        for tgt in tried:
            _mark(stored, tgt)
        ej["tables"] = stored
        row.extracted_json = ej
        flag_modified(row, "extracted_json")
        meta = dict(row.ocr_metadata_json or {})
        ref = dict(meta.get("refine") or {})
        ref.update(refine_patch)
        ref["remaining_flagged"] = _remaining(stored)
        meta["refine"] = ref
        row.ocr_metadata_json = meta
        flag_modified(row, "ocr_metadata_json")
        db.commit()
        return applied


def run_refine_slice(job_id: str, part: int = 1) -> None:
    t0 = time.monotonic()
    bus = None
    try:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            row = db.query(ProcessingResult).filter_by(job_id=job_id).first()
            doc = db.get(Document, job.document_id) if job else None
            if not (job and row and doc):
                return
            ref = dict((row.ocr_metadata_json or {}).get("refine") or {})
            tables = deepcopy((row.extracted_json or {}).get("tables") or [])
        if not tables or ref.get("status") in ("done", "cancelled", "failed"):
            return

        bus = EventBus(job_id)
        bus._seq = bus.last_seq()
        if _cancelled(job_id):
            _persist(job_id, [], [], {"status": "cancelled"})
            bus.emit("refine_completed", status="cancelled", remaining=_remaining(tables))
            return

        first = ref.get("status") == "pending"
        ref.update({"status": "running", "slices": ref.get("slices", 0) + 1})
        _persist(job_id, [], [], ref)
        if first:
            bus.emit("refine_started", flagged=ref.get("flagged_at_start", _remaining(tables)))

        pages = _Pages(doc)
        reader, vlm = CropReader(), None
        improved, vlm_calls = int(ref.get("improved", 0)), int(ref.get("vlm_calls", 0))
        batch: list[Change] = []
        batch_tried: list = []

        def flush() -> None:
            nonlocal improved, batch, batch_tried
            if not batch and not batch_tried:
                return
            applied = _persist(job_id, batch, batch_tried, {"improved": improved, "vlm_calls": vlm_calls})
            for ch in applied:
                bus.emit("cell_refined", table_index=ch.table_index, row=ch.row, col=ch.col, text=ch.text, state=ch.state,
                         confidence=ch.confidence, reasons=ch.reasons, rev=ch.rev, tier=ch.tier)
            bus.emit("refine_progress", queued=len(pending_targets(tables, get_focus(job_id), max(0, settings.FAST_REFINE_VLM_MAX - vlm_calls))), improved=improved,
                     remaining_flagged=_remaining(tables), vlm_calls=vlm_calls)
            batch, batch_tried = [], []

        finished = False
        while True:
            if bus.cancel_requested() or _cancelled(job_id):
                flush()
                _persist(job_id, [], [], {"status": "cancelled"})
                bus.emit("refine_completed", status="cancelled", remaining=_remaining(tables))
                return
            targets = pending_targets(tables, get_focus(job_id), max(0, settings.FAST_REFINE_VLM_MAX - vlm_calls))
            if not targets:
                finished = True
                break
            if time.monotonic() - t0 > settings.FAST_REFINE_SLICE_S:
                break
            tgt = targets[0]
            table = tables[tgt.table_pos]
            change = None
            try:
                img = pages.get(tgt.page)
                if tgt.tier == "t1":
                    change = refine_with_tesseract(table, tgt, img, reader)
                else:
                    if vlm is None:
                        from app.fast.vlm import FieldVlm
                        vlm = FieldVlm()
                    vlm_calls += 1
                    change = refine_with_vlm(table, tgt, img, vlm)
            except Exception as exc:                   # a bad crop or a down model must not stop the rest
                logger.warning("refine_cell_failed", job_id=job_id, row=tgt.row, col=tgt.col, tier=tgt.tier, error=str(exc))
            mark_tried(tables, tgt)
            batch_tried.append(tgt)
            if change:
                if apply_changes(tables, [change]):
                    batch.append(change)
                    if change.state == "accepted":
                        improved += 1
            if len(batch_tried) >= settings.FAST_REFINE_BATCH:
                flush()
        flush()

        if finished:
            _persist(job_id, [], [], {"status": "done", "improved": improved, "vlm_calls": vlm_calls})
            bus.emit("refine_completed", status="done", improved=improved, remaining=_remaining(tables), vlm_calls=vlm_calls)
        else:
            dispatch_refine(job_id, part + 1)
    except Exception as exc:
        logger.error("refine_failed", job_id=job_id, error=str(exc), exc_info=True)
        try:
            _persist(job_id, [], [], {"status": "failed", "error": str(exc)[:200]})
            if bus:
                bus.emit("refine_completed", status="failed", error=str(exc)[:200])
        except Exception:
            pass
