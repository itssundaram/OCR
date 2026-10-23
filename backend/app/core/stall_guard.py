"""
Make a stuck job visible instead of silent.

``stall_guard("label")`` wraps one job. If the job is still running after
``WORKER_STALL_DUMP_S`` seconds, the stack of EVERY thread is written to stderr
(and repeated every interval), so a hang shows exactly which call it is waiting
in (a model call, a subprocess, a DB query...). It never kills anything; the
hard limits live in the calls themselves (Ollama / Tesseract timeouts, RQ job_timeout).

``start_heartbeat("worker")`` logs one line every ``WORKER_HEARTBEAT_S`` seconds
with the job that is running, so a terminal that looks frozen shows it is alive.
"""
from __future__ import annotations

import faulthandler
import sys
import threading
import time
from contextlib import contextmanager

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_current: dict[str, float] = {}
_current_lock = threading.Lock()


@contextmanager
def stall_guard(label: str):
    after = int(getattr(settings, "WORKER_STALL_DUMP_S", 0) or 0)
    with _current_lock:
        _current[label] = time.monotonic()
    armed = False
    if after > 0:
        try:
            faulthandler.dump_traceback_later(after, repeat=True, file=sys.stderr)
            armed = True
        except Exception as exc:                    # never let the guard break a job
            logger.warning("stall_guard_unavailable", error=str(exc))
    try:
        yield
    finally:
        if armed:
            try:
                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass
        with _current_lock:
            started = _current.pop(label, time.monotonic())
        logger.info("job_wall_time", job=label, seconds=round(time.monotonic() - started, 1))


def start_heartbeat(name: str) -> threading.Thread | None:
    every = int(getattr(settings, "WORKER_HEARTBEAT_S", 0) or 0)
    if every <= 0:
        return None

    def _beat() -> None:
        while True:
            time.sleep(every)
            with _current_lock:
                running = {k: round(time.monotonic() - t, 1) for k, t in _current.items()}
            logger.info("worker_heartbeat", worker=name, running=running or "idle")

    t = threading.Thread(target=_beat, name=f"{name}-heartbeat", daemon=True)
    t.start()
    return t
