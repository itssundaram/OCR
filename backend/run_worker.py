import os
import platform
import time

import redis
from rq import Queue, SimpleWorker, Worker

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

configure_logging()
# Tesseract's own OpenMP threads fight with page/crop-parallel OCR; one each is faster overall
os.environ.setdefault("OMP_THREAD_LIMIT", str(settings.TESSERACT_OMP_THREADS))
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
    from app.system.runtime import configure_process
    configure_process("worker")   # detect GPU/CPU/RAM and apply settings nobody set explicitly

    logger.info("worker_initializing_db_engine")
    from app.db.database import engine  # noqa: F401 — side-effect import
    logger.info("worker_db_engine_ready")

    # ── 2. Preload all ML model weights ────────────────────────────────────
    # Loads Tesseract, Surya Layout, Surya OCR, and TATR into RAM/VRAM once.
    # Forked child processes inherit via copy-on-write at zero additional cost.
    logger.info("worker_preloading_models")
    t0 = time.perf_counter()
    try:
        from app.core.model_registry import preload_all_models
        preload_all_models()
        logger.info("worker_models_ready", seconds=round(time.perf_counter() - t0, 1))
    except Exception as exc:
        # a missing optional model must not keep the worker from starting; it loads on first use instead
        logger.error("worker_preload_failed_continuing", error=str(exc), exc_info=True)


def _build_worker(queues, connection):
    """Which RQ worker runs the jobs.

    * Windows: rq-win's WindowsWorker (no fork there).
    * Linux, WORKER_MODE auto/simple (default): SimpleWorker - the job runs INSIDE this process. The classic
      Worker forks a child per job; the parent has already loaded CUDA models (Surya/TATR) by then and a forked
      child that touches CUDA, or an OpenMP thread pool, can deadlock - the "terminal got stuck" symptom.
      In-process also means the preloaded models are used directly (no copy-on-write surprises).
    * WORKER_MODE=fork: the classic forking Worker, if you really want process isolation per job.
    """
    if platform.system() == "Windows":
        from rq_win import WindowsWorker
        return WindowsWorker(queues, connection=connection)
    if settings.WORKER_MODE == "fork":
        return Worker(queues, connection=connection)
    return SimpleWorker(queues, connection=connection)


def main():
    # Initialize DB + models BEFORE starting the worker loop so all forked
    # job subprocesses inherit the already-initialized state.
    _initialize_worker_process()

    conn = redis.from_url(settings.REDIS_URL)
    q = Queue(settings.REDIS_QUEUE_NAME, connection=conn)
    worker = _build_worker([q], connection=conn)
    from app.core.stall_guard import start_heartbeat
    start_heartbeat("worker")
    print(f"Starting RQ worker ({type(worker).__name__}, {platform.system()}) on queue '{settings.REDIS_QUEUE_NAME}'...", flush=True)
    worker.work(with_scheduler=True)


if __name__ == '__main__':
    main()
