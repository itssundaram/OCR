"""
Prefetch: a document starts processing the moment it is chosen, before "Execute extraction" is pressed.

How it is kept invisible and safe
  * The job is a normal ProcessingJob whose `error_code` is "PREFETCH" (no schema change). Every list that people
    see (processing list, dashboard counts, document list) filters it out with `visible_jobs()`.
  * "Execute" calls `commit()`: the marker is cleared and the job becomes an ordinary one. Whatever the worker has
    done so far is kept, so the results are ready (or nearly) when the person opens them.
  * Choosing a different file / template / pipeline calls `discard()`: the marker becomes "PREFETCH_DISCARDED", a
    cancel flag is set so the fast worker stops at its next checkpoint, and the rows are removed as soon as the job
    is no longer running (the reaper finishes that later if the worker is still busy).
  * `discard()` refuses a job that is not a live prefetch, so a late "discard" can never kill a job that was
    already committed (the race between Execute and a change of mind).
  * A prefetch nobody committed (tab closed) is discarded by the reaper after PREFETCH_TTL_SECONDS.
"""
from __future__ import annotations

import datetime
import os

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import DocIntError
from app.core.logging import get_logger
from app.db.models import (
    Asset, Document, DocumentPage, FieldExtraction, PipelineRun, ProcessingEvent, ProcessingJob,
    ProcessingResult, TableExtraction,
)

logger = get_logger(__name__)

MARKER = "PREFETCH"
DISCARDED = "PREFETCH_DISCARDED"
HIDDEN = (MARKER, DISCARDED)
TERMINAL = ("COMPLETED", "WARNING", "FAILED")


def visible_jobs():
    """SQL condition: jobs that people may see (everything except live and discarded prefetches)."""
    return or_(ProcessingJob.error_code.is_(None), ProcessingJob.error_code.notin_(HIDDEN))


def is_prefetch(job: ProcessingJob | None) -> bool:
    return bool(job) and job.error_code == MARKER


def mark(job: ProcessingJob) -> None:
    job.error_code = MARKER


def commit(db: Session, job_id: str) -> ProcessingJob:
    """Execute pressed: the prefetch becomes an ordinary job. Idempotent for a job that is already ordinary."""
    job = db.get(ProcessingJob, job_id)
    if job is None or job.error_code == DISCARDED:
        raise DocIntError("This prefetch is gone; upload the document again.", "PREFETCH_GONE")
    if job.error_code == MARKER:
        job.error_code = None
        db.commit()
        logger.info("prefetch_committed", job_id=job_id, status=job.status)
    return job


def _request_cancel(job_id: str) -> bool:
    """Best effort: tell the fast worker to stop and drop the job from the queue if nobody started it.
    Returns True when the job was still waiting in the queue and has been removed (nothing is running)."""
    removed = False
    try:
        from app.workers.queue import redis_conn
        if redis_conn is None:
            return False
        from app.fast.events import cancel_key
        redis_conn.set(cancel_key(job_id), "1", ex=3600)
        try:
            from rq.job import Job
            rq_job = Job.fetch(job_id, connection=redis_conn)
            if rq_job.get_status(refresh=True) in ("queued", "deferred", "scheduled"):
                rq_job.cancel()
                removed = True
        except Exception:
            pass                                           # not in the queue (already running or finished)
    except Exception as exc:
        logger.warning("prefetch_cancel_failed", job_id=job_id, error=str(exc)[:160])
    return removed


def discard(db: Session, job_id: str) -> str:
    """The person chose something else. Returns "purged", "discarding" (still running; the reaper finishes it) or
    "gone". Refuses (PREFETCH_NOT_DISCARDABLE) a job that is not a live prefetch."""
    job = db.get(ProcessingJob, job_id)
    if job is None:
        return "gone"
    if job.error_code == DISCARDED:
        if job.status in TERMINAL:
            return "purged" if purge(db, job) else "discarding"
        return "discarding"
    if job.error_code != MARKER:
        raise DocIntError("This job was already started; it cannot be discarded as a prefetch.", "PREFETCH_NOT_DISCARDABLE")
    job.error_code = DISCARDED
    db.commit()
    removed = _request_cancel(job_id)
    logger.info("prefetch_discarded", job_id=job_id, status=job.status, removed_from_queue=removed)
    if job.status in TERMINAL or (job.status == "QUEUED" and removed):
        return "purged" if purge(db, job) else "discarding"
    return "discarding"


def purge(db: Session, job: ProcessingJob) -> bool:
    """Remove a hidden job, its rows and its stored files. Never touches a visible job."""
    if job.error_code not in HIDDEN:
        return False
    job_id, doc_id = job.id, job.document_id
    try:
        asset_ids: set[str] = set()
        for (aid,) in db.execute(select(DocumentPage.image_asset_id).where(DocumentPage.document_id == doc_id)):
            if aid:
                asset_ids.add(aid)
        for model in (FieldExtraction, TableExtraction):
            for (aid,) in db.execute(select(model.crop_asset_id).where(model.job_id == job_id)):
                if aid:
                    asset_ids.add(aid)
        asset_paths = [p for (p,) in db.execute(select(Asset.file_path).where(Asset.id.in_(asset_ids)))] if asset_ids else []
        doc = db.get(Document, doc_id)
        doc_path = doc.file_path if doc else None
        other_jobs = db.execute(select(ProcessingJob.id).where(ProcessingJob.document_id == doc_id, ProcessingJob.id != job_id)).first()

        for model in (ProcessingEvent, FieldExtraction, TableExtraction):
            db.execute(delete(model).where(model.job_id == job_id))
        db.execute(delete(ProcessingResult).where(ProcessingResult.job_id == job_id))
        db.execute(delete(PipelineRun).where(PipelineRun.job_id == job_id))
        db.execute(delete(ProcessingJob).where(ProcessingJob.id == job_id))
        if not other_jobs:
            db.execute(delete(DocumentPage).where(DocumentPage.document_id == doc_id))
            if asset_ids:
                db.execute(delete(Asset).where(Asset.id.in_(asset_ids)))
            db.execute(delete(Document).where(Document.id == doc_id))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("prefetch_purge_failed", job_id=job_id, error=str(exc)[:200])
        return False
    if not other_jobs:
        for path in [doc_path, *asset_paths]:
            try:
                if path and os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass
    logger.info("prefetch_purged", job_id=job_id)
    return True


def reap(db: Session, now: datetime.datetime | None = None) -> int:
    """Discard prefetches nobody committed and purge discarded ones that have stopped. Returns how many were handled."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    ttl = datetime.timedelta(seconds=int(getattr(settings, "PREFETCH_TTL_SECONDS", 900)))
    hold = datetime.timedelta(seconds=int(settings.JOB_TIMEOUT_SECONDS) + 180)
    n = 0
    rows = db.execute(select(ProcessingJob).where(ProcessingJob.error_code.in_(HIDDEN))).scalars().all()
    for job in rows:
        created = job.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=datetime.timezone.utc)
        age = (now - created) if created else datetime.timedelta(0)
        if job.error_code == MARKER and age > ttl:
            discard(db, job.id)
            n += 1
        elif job.error_code == DISCARDED and (job.status in TERMINAL or age > hold):
            n += 1 if purge(db, job) else 0
    return n
