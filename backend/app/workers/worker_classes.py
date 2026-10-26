"""
Pick the RQ worker class that suits the operating system - automatically.

RQ enforces a job's time limit with SIGALRM, which Windows does not have, and forks a process per job, which Windows
cannot do. Everything here is chosen from the platform at start-up; FAST_WORKER_TIMEOUT / WORKER_MODE only override.

  Linux / macOS : SimpleWorker with RQ's signal timer (jobs run in the worker's own process: models stay loaded)
  Windows       : SimpleWorker with a thread timer (same: models stay loaded, no process start per job)
  WORKER_MODE=fork : one process per job (RQ's classic Worker; on Windows rq-win's WindowsWorker)
"""
from __future__ import annotations

import os
import signal

from rq import SimpleWorker, Worker
from rq.timeouts import TimerDeathPenalty

from app.core.config import settings


class TimerSimpleWorker(SimpleWorker):
    """SimpleWorker that enforces the job time limit with a thread timer instead of SIGALRM."""
    death_penalty_class = TimerDeathPenalty


def use_timer() -> bool:
    mode = (settings.FAST_WORKER_TIMEOUT or "auto").lower()
    if mode == "auto":
        return os.name == "nt" or not hasattr(signal, "SIGALRM")
    return mode == "timer"


def worker_class():
    if settings.WORKER_MODE == "fork":
        if os.name == "nt":
            from rq_win import WindowsWorker
            return WindowsWorker
        return Worker
    return TimerSimpleWorker if use_timer() else SimpleWorker


def build_worker(queues, connection, **kwargs):
    return worker_class()(queues, connection=connection, **kwargs)
