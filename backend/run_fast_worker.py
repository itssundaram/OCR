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

import platform

import redis
from rq import Queue, SimpleWorker

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


def main() -> None:
    from app.db.database import engine  # noqa: F401  (opens the DB pool once)
    from app.fast.warmup import run_startup_checks
    run_startup_checks()

    conn = redis.from_url(settings.REDIS_URL)
    queue = Queue(settings.FAST_QUEUE_NAME, connection=conn)
    worker = SimpleWorker([queue], connection=conn)
    print(f"Starting fast_ocr worker ({platform.system()}) on queue '{settings.FAST_QUEUE_NAME}' ...")
    worker.work()


if __name__ == "__main__":
    main()
