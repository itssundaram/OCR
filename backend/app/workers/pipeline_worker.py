"""
One worker process. Started by run_worker.py, once per pipeline (and one "general"):

    python -m app.workers.pipeline_worker --name fast_ocr

It listens only on its own queue, so a heavy surya job can never delay a fast_ocr upload. It loads only the models its
pipeline needs. The same code runs on Windows and Linux (see worker_classes.py).
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
import threading

import redis

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)
os.environ.setdefault("OMP_THREAD_LIMIT", str(settings.TESSERACT_OMP_THREADS))

GENERAL = "general"


def queues_for(name: str) -> list[str]:
    from app.workers.routing import general_queue, queue_name
    if name == GENERAL:
        return [general_queue()]
    if name == "fast_ocr":
        # a waiting upload is always served before background refinement
        return [settings.FAST_QUEUE_NAME, settings.FAST_REFINE_QUEUE_NAME]
    return [queue_name(name)]


def _prepare(name: str) -> None:
    """Load what this pipeline needs now; everything else loads on its first job."""
    if name == "fast_ocr":
        from app.fast.warmup import run_startup_checks, warm_vlm
        run_startup_checks(vlm=False)
        threading.Thread(target=warm_vlm, name="vlm-warmup", daemon=True).start()
        from app.fast.worker_supervisor import publish_build
        publish_build(redis.from_url(settings.REDIS_URL))
    elif name in ("learned_anchor", "tesseract", "fast_easyocr"):
        try:
            from app.core.model_registry import get_tesseract_engine
            eng = get_tesseract_engine()
            if not getattr(eng, "_is_loaded", True):
                eng.load_model()
        except Exception as exc:
            logger.warning("worker_prepare_tesseract_failed", worker=name, error=str(exc)[:200])


def reclaim_orphans(conn, queues: list[str]) -> int:
    """A previous worker on these queues died mid-job (killed, crashed, power loss): its job is still 'started' in
    Redis and PROCESSING in the database. Nobody else serves these queues, so no job here is really running: mark
    those jobs failed so the review page shows an error and Re-run, instead of spinning."""
    from rq import Queue
    from rq.registry import StartedJobRegistry
    from rq.job import Job
    n = 0
    for qn in queues:
        try:
            reg = StartedJobRegistry(queue=Queue(qn, connection=conn))
            for jid in list(reg.get_job_ids()):
                try:
                    job = Job.fetch(jid, connection=conn)
                    biz = job.args[0] if job.args else None
                    if biz:
                        from app.services.extractor import mark_job_permanently_failed
                        mark_job_permanently_failed(biz, "The worker running this job stopped unexpectedly. Press Re-run.")
                    reg.remove(job, delete_job=False)
                    n += 1
                except Exception as exc:
                    logger.warning("orphan_job_cleanup_failed", job_id=jid, error=str(exc)[:160])
        except Exception as exc:
            logger.warning("orphan_registry_scan_failed", queue=qn, error=str(exc)[:160])
    if n:
        logger.warning("orphan_jobs_failed", count=n, queues=queues)
    return n


def main(name: str) -> None:
    from rq import Queue
    from app.system.runtime import configure_process
    configure_process("fast_worker" if name == "fast_ocr" else f"worker:{name}")
    from app.db.database import engine  # noqa: F401  (opens the DB pool once)
    from app.workers.routing import start_presence
    from app.workers.worker_classes import build_worker
    from app.core.stall_guard import start_heartbeat

    from app.workers.launcher import watch_launcher
    watch_launcher()
    conn = redis.from_url(settings.REDIS_URL)
    qnames = queues_for(name)
    _prepare(name)
    reclaim_orphans(conn, qnames)
    start_heartbeat(name)
    worker = build_worker([Queue(q, connection=conn) for q in qnames], connection=conn)
    start_presence(conn, qnames)
    from app.core.build_info import build_id
    logger.info("worker_ready", worker=name, queues=qnames, klass=type(worker).__name__, system=platform.system(), build=build_id())
    print(f"listening on {', '.join(qnames)} ({type(worker).__name__}, {platform.system()})", flush=True)
    # the scheduler runs retries; one is enough, so only the general worker owns it
    worker.work(with_scheduler=(name == GENERAL))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="a pipeline name, or 'general'")
    sys.exit(main(ap.parse_args().name))
