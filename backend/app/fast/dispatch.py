"""
Queue the fast_ocr job.

It runs on its own queue (settings.FAST_QUEUE_NAME) served by run_fast_worker.py,
which keeps the models warm and runs each job in the worker's own process.
If no such worker is running the job goes to the normal queue instead, where
the existing worker can still run it (slower: it forks per job).

Redis down => 503. fast_ocr never silently runs a job inside the web process
(set FAST_ALLOW_BACKGROUND_FALLBACK=true to get the old behaviour back).
"""
from __future__ import annotations

from app.core.config import settings
from app.core.exceptions import DocIntError
from app.core.logging import get_logger

logger = get_logger(__name__)

TASK_PATH = "app.fast.runner.run_fast_job"


def _worker_listening(conn, queue_name: str) -> bool:
    from app.fast.worker_supervisor import worker_listening
    return worker_listening(conn, queue_name)


def dispatch_fast_job(job_id: str, force_relearn: bool, background_tasks=None) -> str:
    from app.workers.queue import redis_conn

    if redis_conn is not None:
        try:
            from rq import Callback, Queue
            fast_ready = _worker_listening(redis_conn, settings.FAST_QUEUE_NAME)
            if not fast_ready:
                from app.fast.worker_supervisor import ensure_fast_worker
                fast_ready = ensure_fast_worker(redis_conn, wait_s=settings.FAST_AUTOSTART_WAIT_S)
            name = settings.FAST_QUEUE_NAME if fast_ready else settings.REDIS_QUEUE_NAME
            if not fast_ready:
                logger.warning("fast_worker_not_listening", job_id=job_id, queue=name,
                               hint="the automatic start did not produce a worker: see data/logs/fast_worker.log or run `python run_fast_worker.py`; "
                                    "the default worker is slower to pick this up")
            Queue(name, connection=redis_conn).enqueue(
                TASK_PATH, job_id, force_relearn,
                job_id=job_id, job_timeout=settings.FAST_JOB_TIMEOUT_S,
                on_failure=Callback("app.workers.tasks.job_failed_callback"),
            )
            logger.info("fast_job_enqueued", job_id=job_id, queue=name)
            return f"redis:{name}"
        except Exception as exc:
            logger.error("fast_enqueue_failed", job_id=job_id, error=str(exc))

    if settings.FAST_ALLOW_BACKGROUND_FALLBACK and background_tasks is not None:
        from app.fast.runner import run_fast_job
        background_tasks.add_task(run_fast_job, job_id, force_relearn)
        return "background_task"

    from app.services.extractor import mark_job_permanently_failed
    mark_job_permanently_failed(job_id, "Job queue (Redis) is unavailable.")
    raise DocIntError("The job queue is unavailable; the upload was not processed.", "QUEUE_UNAVAILABLE")
