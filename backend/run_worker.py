import platform
import redis
from rq import Queue, Worker

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


def _initialize_worker_process() -> None:
    """
    Run ONCE in the parent worker process before any job forks.

    RQ uses os.fork() on Linux — forked child processes inherit the parent's
    entire memory space (copy-on-write). By initializing the DB engine and
    loading all model weights here, in the parent, every job child gets them
    for free without any re-creation or re-load cost.

    This is the correct, fundamental fix:
      • DB engine created ONCE  → no 'database_engine_creating' spam per job
      • Models loaded ONCE      → no multi-second cold start per document
    """
    # ── 1. DB Engine singleton ─────────────────────────────────────────────
    # Importing `engine` triggers _build_engine() exactly once in this process.
    # All forked job children inherit the already-connected pool.
    logger.info("worker_initializing_db_engine")
    from app.db.database import engine  # noqa: F401 — side-effect import
    logger.info("worker_db_engine_ready")

    # ── 2. Preload all ML model weights ────────────────────────────────────
    # Loads Tesseract, Surya Layout, Surya OCR, and TATR into RAM/VRAM once.
    # Forked child processes inherit via copy-on-write at zero additional cost.
    logger.info("worker_preloading_models")
    from app.core.model_registry import preload_all_models
    preload_all_models()
    logger.info("worker_models_ready")


def _build_worker(queues, connection):
    """RQ's default Worker relies on os.fork(), which doesn't exist on
    Windows — hence the rq-win WindowsWorker used on the dev box. On Linux
    (the GPU server), the standard rq.Worker is the right, simpler choice;
    only import rq_win when actually on Windows so this file runs on both
    without requiring rq-win to be installed on Linux at all."""
    if platform.system() == "Windows":
        from rq_win import WindowsWorker
        return WindowsWorker(queues, connection=connection)
    return Worker(queues, connection=connection)


def main():
    # Initialize DB + models BEFORE starting the worker loop so all forked
    # job subprocesses inherit the already-initialized state.
    _initialize_worker_process()

    conn = redis.from_url(settings.REDIS_URL)
    q = Queue(settings.REDIS_QUEUE_NAME, connection=conn)
    worker = _build_worker([q], connection=conn)
    print(f"Starting RQ worker ({platform.system()}) on queue '{settings.REDIS_QUEUE_NAME}'...")
    worker.work(with_scheduler=True)


if __name__ == '__main__':
    main()
