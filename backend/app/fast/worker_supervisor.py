"""
Keep a fast_ocr worker alive.

Why: a fast_ocr job is only quick on its own worker (`run_fast_worker.py`: models stay loaded, the job runs in that
process). Without one the job is handed to the default queue, whose worker may start a fresh process per job
(always on Windows) and pays the import / model start-up cost every time - the "upload takes ages" symptom, logged
as `fast_worker_not_listening`. The Windows launcher also never started the fast worker.

`ensure_fast_worker()` starts one when none is listening (and FAST_AUTOSTART_WORKER is on). A Redis lock keeps
several API processes from starting several workers. The child's output goes to <LOG_DIR>/fast_worker.log.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_LOCK_KEY = "docint:fast_worker:spawn_lock"
BUILD_KEY = "docint:fast_worker:build"
ALIVE_KEY = "docint:fast_worker:alive"     # refreshed every few seconds by the worker itself; gone ~ALIVE_TTL_S after it dies
STOP_KEY = "docint:fast_worker:stop"       # value = the build a worker must run; any worker on another build leaves when idle
ALIVE_TTL_S = 45
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_child: subprocess.Popen | None = None


def worker_listening(conn, queue_name: str | None = None) -> bool:
    """Is a fast worker ALIVE right now?

    RQ's own worker registry is not enough: a worker that is killed (always the case when Windows stops a process,
    and on a crash or power loss anywhere) stays registered for several minutes, so uploads were queued for a
    worker that no longer existed and sat at QUEUED. The fast worker therefore refreshes ALIVE_KEY every few seconds
    with a short expiry: a dead worker stops counting within ALIVE_TTL_S seconds, on every operating system.
    """
    try:
        if queue_name in (None, settings.FAST_QUEUE_NAME):
            return bool(conn.exists(ALIVE_KEY))
        from rq import Queue, Worker
        return Worker.count(connection=conn, queue=Queue(queue_name, connection=conn)) > 0
    except Exception:
        return False


def publish_alive(conn) -> None:
    """Called repeatedly by the worker: 'I am here, running this build'."""
    from app.core.build_info import build_id
    try:
        conn.set(ALIVE_KEY, f"{os.getpid()}|{build_id()}", ex=ALIVE_TTL_S)
    except Exception:
        pass


def clear_alive(conn) -> None:
    """Called by a worker that is leaving on purpose (only removes its own mark)."""
    try:
        mine = f"{os.getpid()}|"
        cur = conn.get(ALIVE_KEY)
        cur = cur.decode() if isinstance(cur, bytes) else cur
        if cur and cur.startswith(mine):
            conn.delete(ALIVE_KEY)
    except Exception:
        pass


def should_retire(conn, my_build: str) -> bool:
    """A worker on another build than the one the API asked for must leave (when idle)."""
    try:
        want = conn.get(STOP_KEY)
        want = want.decode() if isinstance(want, bytes) else want
        return bool(want) and want != my_build
    except Exception:
        return False


def _log_path() -> Path:
    try:
        base = Path(settings.storage_path) / "logs"
    except Exception:
        base = _BACKEND_DIR / "data" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base / "fast_worker.log"


def _spawn() -> subprocess.Popen:
    out = open(_log_path(), "ab")
    kwargs: dict = {"cwd": str(_BACKEND_DIR), "stdout": out, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if platform.system() == "Windows":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True            # survives the API's reload / Ctrl+C of the parent shell group
    return subprocess.Popen([sys.executable, str(_BACKEND_DIR / "run_fast_worker.py")], **kwargs)


def publish_build(conn) -> None:
    """Called by the worker itself at start-up: which code it runs."""
    from app.core.build_info import build_id
    try:
        conn.set(BUILD_KEY, build_id())
    except Exception:
        pass


def _retire_stale_workers(conn) -> bool:
    """A worker started before the last code change still runs the old code. Ask it to leave; True if one was asked.

    The request is a Redis flag the worker reads itself and acts on when it is idle. RQ's shutdown command is NOT
    used on Windows: there it ends in os.kill(), which terminates the process hard and leaves a ghost registration.
    """
    from app.core.build_info import build_id
    try:
        theirs = conn.get(BUILD_KEY)
        theirs = theirs.decode() if isinstance(theirs, bytes) else theirs
        if theirs == build_id():
            return False
        if not conn.exists(ALIVE_KEY):
            return False
        conn.set(STOP_KEY, build_id(), ex=600)
        if os.name != "nt":                           # workers from before this flag existed only know the RQ command
            from rq import Queue, Worker
            from rq.command import send_shutdown_command
            for w in Worker.all(connection=conn, queue=Queue(settings.FAST_QUEUE_NAME, connection=conn)):
                send_shutdown_command(conn, w.name)
        logger.warning("fast_worker_stale_retired", worker_build=theirs, api_build=build_id())
        conn.delete(BUILD_KEY)
        conn.delete(ALIVE_KEY)                        # a replacement may start now; the old one leaves when its job is done
        return True
    except Exception as exc:
        logger.warning("fast_worker_retire_failed", error=str(exc)[:200])
        return False


def ensure_fast_worker(conn, wait_s: float = 0.0, retire_stale: bool = False) -> bool:
    """True when a fast worker is listening (after starting one and waiting up to `wait_s` seconds if needed).
    retire_stale: a listening worker that runs another build than this process is stopped and replaced."""
    global _child
    if conn is None:
        return False
    if worker_listening(conn):
        if not (retire_stale and settings.FAST_AUTOSTART_WORKER and _retire_stale_workers(conn)):
            return True
        deadline = time.monotonic() + 8.0             # let the old worker finish its job and leave the queue
        while time.monotonic() < deadline and worker_listening(conn):
            time.sleep(0.5)
        if worker_listening(conn):
            return True
    if not settings.FAST_AUTOSTART_WORKER:
        return False
    try:
        if _child is not None and _child.poll() is None:
            pass                                      # started by this process, still booting
        elif conn.set(_LOCK_KEY, str(os.getpid()), nx=True, ex=45):
            _child = _spawn()
            logger.info("fast_worker_autostarted", pid=_child.pid, log=str(_log_path()))
        # else: another API process is already starting it - just wait for it below
    except Exception as exc:
        logger.warning("fast_worker_autostart_failed", error=str(exc)[:200])
        return False
    deadline = time.monotonic() + max(0.0, wait_s)
    while time.monotonic() < deadline:
        if worker_listening(conn):
            return True
        if _child is not None and _child.poll() is not None:
            logger.warning("fast_worker_exited_early", code=_child.returncode, log=str(_log_path()),
                           hint="open the log file: a failed start-up check (Tesseract / weights) stops the worker")
            return False
        time.sleep(0.5)
    return worker_listening(conn)
