"""
Worker for the fast_ocr pipeline.

    python run_fast_worker.py

* listens on FAST_QUEUE_NAME only (the existing run_worker.py is unchanged)
* runs each job IN this process (rq SimpleWorker): no fork per job, so nothing
  that was initialised here (DB pool, VLM keep-alive, caches) is lost or
  duplicated, and no CUDA state is inherited by a child
* checks Tesseract / weights / Ollama before it accepts a job
"""
from __future__ import annotations

import os
import platform
import threading

import redis
from rq import Queue, SimpleWorker

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)
os.environ.setdefault("OMP_THREAD_LIMIT", str(settings.TESSERACT_OMP_THREADS))


def main() -> None:
    from app.system.runtime import configure_process
    configure_process("fast_worker")
    from app.db.database import engine  # noqa: F401  (opens the DB pool once)
    from app.fast.warmup import run_startup_checks, warm_vlm
    run_startup_checks(vlm=False)          # cheap, must-pass checks (Tesseract, weights) - milliseconds
    # Loading the vision model into Ollama can take a minute. It used to run BEFORE the worker registered,
    # so uploads in that window were sent to the slow default queue. Now the worker registers first and the
    # model warms in the background; a job that needs the VLM before it is warm simply waits for Ollama.
    threading.Thread(target=warm_vlm, name="vlm-warmup", daemon=True).start()
    from app.core.stall_guard import start_heartbeat
    start_heartbeat("fast_worker")

    conn = redis.from_url(settings.REDIS_URL)
    from app.fast.worker_supervisor import publish_build
    publish_build(conn)
    from app.core.build_info import build_id
    logger.info("fast_worker_build", build=build_id())
    # order = priority: a waiting upload is always served before background refinement
    queues = [Queue(settings.FAST_QUEUE_NAME, connection=conn), Queue(settings.FAST_REFINE_QUEUE_NAME, connection=conn)]
    worker = SimpleWorker(queues, connection=conn)
    print(f"Starting fast_ocr worker ({platform.system()}) on queues '{settings.FAST_QUEUE_NAME}', '{settings.FAST_REFINE_QUEUE_NAME}' ...")
    worker.work()


if __name__ == "__main__":
    main()
