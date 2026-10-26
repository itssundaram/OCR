"""
Which queue a job goes to, and whether anything is serving it.

Every pipeline has its own queue and its own worker (started by run_worker.py). A worker proves it is alive by
refreshing a short-lived Redis key; a worker that is killed or crashes stops counting within ALIVE_TTL_S seconds,
on every operating system (RQ's own worker registry keeps a dead worker for minutes). A job is queued for its
pipeline's worker only while that worker is alive; otherwise it goes to the general queue, which always has a
worker, so an upload never waits for a worker that is gone.
"""
from __future__ import annotations

import os
import threading

from app.core.config import settings

ALIVE_TTL_S = 45
FAST_ALIVE_KEY = "docint:fast_worker:alive"          # the fast_ocr worker's key (kept for run_fast_worker.py)
LAUNCHER_KEY = "docint:launcher"
LAUNCHER_STATE_KEY = "docint:launcher:state"

# pipeline name -> default; the launcher only starts the ones that are installed and enabled
KNOWN_PIPELINES = ("fast_ocr", "learned_anchor", "surya", "tesseract", "fast_easyocr", "fast_docling")


def general_queue() -> str:
    return settings.REDIS_QUEUE_NAME


def queue_name(pipeline: str) -> str:
    """The queue a pipeline's own worker listens on."""
    if pipeline == "fast_ocr":
        return settings.FAST_QUEUE_NAME
    return f"{settings.REDIS_QUEUE_NAME}-{pipeline}"


def dedicated_queues() -> list[str]:
    return [queue_name(p) for p in KNOWN_PIPELINES]


def alive_key(queue: str) -> str:
    if queue == settings.FAST_QUEUE_NAME:
        return FAST_ALIVE_KEY
    return f"docint:worker:alive:{queue}"


def queue_alive(conn, queue: str) -> bool:
    try:
        return bool(conn.exists(alive_key(queue)))
    except Exception:
        return False


def pick_queue(conn, pipeline: str | None) -> str:
    """Pipeline's own queue when its worker is alive, else the general queue."""
    if not pipeline or pipeline not in KNOWN_PIPELINES or conn is None:
        return general_queue()
    q = queue_name(pipeline)
    return q if queue_alive(conn, q) else general_queue()


def publish_alive(conn, queues: list[str]) -> None:
    from app.core.build_info import build_id
    mark = f"{os.getpid()}|{build_id()}"
    for q in queues:
        try:
            conn.set(alive_key(q), mark, ex=ALIVE_TTL_S)
        except Exception:
            pass


def clear_alive(conn, queues: list[str]) -> None:
    """Remove this process's marks (a worker leaving on purpose); never another process's."""
    mine = f"{os.getpid()}|"
    for q in queues:
        try:
            cur = conn.get(alive_key(q))
            cur = cur.decode() if isinstance(cur, bytes) else cur
            if cur and cur.startswith(mine):
                conn.delete(alive_key(q))
        except Exception:
            pass


def start_presence(conn, queues: list[str], every: float = 10.0) -> threading.Event:
    """Keep this worker's alive marks fresh until the returned event is set."""
    stop = threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            publish_alive(conn, queues)
            stop.wait(every)

    publish_alive(conn, queues)
    threading.Thread(target=_loop, name="worker-presence", daemon=True).start()
    return stop
