"""
Stuck-job reaper.

A worker that is killed (OOM, power loss, deploy) leaves its job at PROCESSING in
the database forever, so the review page spins for ever. RQ itself kills a job that
runs past its timeout, so any job still PROCESSING well beyond that timeout has lost
its worker. Mark those FAILED so the user sees an error and can press Re-run.
"""
from __future__ import annotations

import asyncio
import datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import SessionLocal
from app.db.models import ProcessingJob

logger = get_logger(__name__)

GRACE_SECONDS = 180
INTERVAL_SECONDS = 120


def stuck_cutoff(now: datetime.datetime | None = None) -> datetime.datetime:
    limit = max(int(settings.JOB_TIMEOUT_SECONDS), int(getattr(settings, "FAST_JOB_TIMEOUT_S", 0) or 0))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now - datetime.timedelta(seconds=limit + GRACE_SECONDS)


def reap_stuck_jobs() -> int:
    """Mark PROCESSING jobs that outlived their timeout as FAILED. Returns how many."""
    from app.services.extractor import mark_job_permanently_failed

    cutoff = stuck_cutoff()
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(ProcessingJob.id, ProcessingJob.started_at).where(ProcessingJob.status == "PROCESSING")
            ).all()
        ids = []
        for jid, started in rows:
            if started is None:
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=datetime.timezone.utc)
            if started < cutoff:
                ids.append(jid)
        for jid in ids:
            mark_job_permanently_failed(jid, "The worker processing this job stopped responding. Press Re-run to try again.")
        if ids:
            logger.warning("stuck_jobs_reaped", count=len(ids), job_ids=ids[:20])
        return len(ids)
    except Exception as exc:
        logger.warning("reaper_failed", error=str(exc))
        return 0


def reap_prefetches() -> int:
    """Discard prefetches nobody committed and purge discarded ones that have stopped (see app/services/prefetch.py)."""
    try:
        from app.services import prefetch
        with SessionLocal() as db:
            return prefetch.reap(db)
    except Exception as exc:
        logger.warning("prefetch_reaper_failed", error=str(exc))
        return 0


async def reaper_loop() -> None:
    while True:
        try:
            await asyncio.get_running_loop().run_in_executor(None, reap_stuck_jobs)
            await asyncio.get_running_loop().run_in_executor(None, reap_prefetches)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let the loop die
            logger.warning("reaper_loop_error", error=str(exc))
        await asyncio.sleep(INTERVAL_SECONDS)
