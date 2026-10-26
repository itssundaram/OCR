"""
run_worker.py - the one command that runs every worker.

    python run_worker.py                    # one worker per pipeline + the general worker
    python run_worker.py --only fast_ocr    # just these (comma list)
    python run_worker.py --list             # show what would start, then exit

It starts each worker as its own process (app/workers/pipeline_worker.py), prefixes every output line with the
worker's name, writes a log file per worker, restarts a worker that crashes (2, 4, 8 ... seconds apart), and stops
them all on Ctrl+C (or a plain kill / service stop). The same code runs on Windows and Linux. Only one launcher runs at a time.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.core.config import settings

BACKEND = Path(__file__).resolve().parents[2]
LAUNCHER_TTL_S = 30

# extra packages a pipeline needs; a pipeline whose package is missing is skipped (the stable install never breaks)
REQUIRES = {"fast_easyocr": "easyocr", "fast_docling": "docling", "surya": "surya"}


def _log_dir() -> Path:
    try:
        d = Path(settings.storage_path) / "logs"
    except Exception:
        d = BACKEND / "data" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def plan(only: str | None = None) -> tuple[list[str], list[tuple[str, str]]]:
    """(workers to start, [(pipeline, why skipped)])."""
    from app.workers.routing import KNOWN_PIPELINES
    spec = (only or settings.WORKER_PIPELINES or "auto").strip()
    wanted = list(KNOWN_PIPELINES) if spec.lower() in ("auto", "all", "") else [s.strip() for s in spec.split(",") if s.strip()]
    start, skipped = [], []
    for p in wanted:
        if p not in KNOWN_PIPELINES:
            skipped.append((p, "unknown pipeline"))
            continue
        pkg = REQUIRES.get(p)
        if pkg and importlib.util.find_spec(pkg) is None:
            skipped.append((p, f"package '{pkg}' is not installed"))
            continue
        start.append(p)
    if settings.WORKER_GENERAL or not start:
        start.append("general")
    return start, skipped


class Child:
    def __init__(self, name: str):
        self.name = name
        self.proc: subprocess.Popen | None = None
        self.restarts = 0
        self.started_at = 0.0
        self.next_start = 0.0
        self.log = _log_dir() / f"worker_{name}.log"

    def start(self) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", DOCINT_PARENT_PID=str(os.getpid()))
        kw: dict = {"cwd": str(BACKEND), "env": env, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
        if os.name == "nt":
            kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)   # Ctrl+C goes to the launcher only
        self.proc = subprocess.Popen([sys.executable, "-m", "app.workers.pipeline_worker", "--name", self.name], **kw)
        self.started_at = time.monotonic()
        threading.Thread(target=self._pump, args=(self.proc,), name=f"pump-{self.name}", daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        try:
            with open(self.log, "ab") as fh:
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    fh.write(raw if raw.endswith(b"\n") else raw + b"\n")
                    fh.flush()
                    if line:
                        print(f"[{self.name}] {line}", flush=True)
        except Exception:
            pass

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if not self.alive():
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _stop_on_signals() -> None:
    """kill <pid>, `docker stop`, systemd stop and closing the console window end the launcher the same way Ctrl+C does,
    so its workers are stopped with it instead of being left running on their own."""
    import signal

    def _raise(_signum, _frame):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGHUP", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _raise)
        except (ValueError, OSError):          # not the main thread / not supported here
            pass


def parent_alive(parent_pid: int) -> bool:
    """Is the launcher that started this worker still running? (Used by the worker's own watchdog.)"""
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            handle = k32.OpenProcess(0x00100000, False, int(parent_pid))      # SYNCHRONIZE
            if not handle:
                return False
            try:
                return k32.WaitForSingleObject(handle, 0) == 0x102             # WAIT_TIMEOUT = still running
            finally:
                k32.CloseHandle(handle)
        except Exception:
            return True                                                        # cannot tell: do not stop the worker
    return os.getppid() == int(parent_pid)


def watch_launcher(interval_s: float = 3.0) -> None:
    """In a worker started by run_worker.py: exit when the launcher disappears (it was killed hard, so it could not
    stop its workers). A worker started by hand (no DOCINT_PARENT_PID) is left alone."""
    raw = os.environ.get("DOCINT_PARENT_PID")
    if not raw or not raw.isdigit():
        return
    parent = int(raw)

    def _loop():
        while True:
            time.sleep(interval_s)
            if not parent_alive(parent):
                print("launcher is gone; stopping this worker", flush=True)
                os._exit(0)
    threading.Thread(target=_loop, name="launcher-watch", daemon=True).start()


def _acquire(conn) -> bool:
    """Only one launcher at a time. A crashed launcher's lock expires in LAUNCHER_TTL_S seconds."""
    from app.workers.routing import LAUNCHER_KEY
    me = f"{os.getpid()}"
    if conn.set(LAUNCHER_KEY, me, nx=True, ex=LAUNCHER_TTL_S):
        return True
    holder = conn.get(LAUNCHER_KEY)
    wait = int(os.environ.get("DOCINT_LAUNCHER_WAIT_S", LAUNCHER_TTL_S + 5))
    print(f"Another run_worker.py is already running (pid {holder.decode() if holder else '?'}). "
          f"If you just stopped one, waiting up to {wait}s for its lock to expire ...", flush=True)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(1)
        if conn.set(LAUNCHER_KEY, me, nx=True, ex=LAUNCHER_TTL_S):
            return True
    return False


def run(only: str | None = None) -> int:
    import redis
    from app.workers import routing
    names, skipped = plan(only)
    print(f"DOCINT workers on {platform.system()}: {', '.join(names)}", flush=True)
    for p, why in skipped:
        print(f"  skipped {p}: {why}", flush=True)

    conn = redis.from_url(settings.REDIS_URL)
    try:
        conn.ping()
    except Exception as exc:
        print(f"Cannot reach Redis at {settings.REDIS_URL}: {exc}", flush=True)
        return 2
    if not _acquire(conn):
        print("Another run_worker.py is still holding the lock; stopping.", flush=True)
        return 3

    from app.workers.pipeline_worker import queues_for
    _stop_on_signals()
    children = [Child(n) for n in names]
    for c in children:
        c.start()
    print("All workers started. Press Ctrl+C to stop them.", flush=True)
    max_backoff = max(2, int(settings.WORKER_RESTART_MAX_BACKOFF_S))
    try:
        while True:
            now = time.monotonic()
            conn.set(routing.LAUNCHER_KEY, str(os.getpid()), ex=LAUNCHER_TTL_S)
            state = {}
            for c in children:
                if c.alive():
                    if now - c.started_at > 60:
                        c.restarts = 0                      # it has been healthy: forget old crashes
                elif c.proc is not None and c.next_start == 0.0:
                    code = c.proc.returncode
                    c.restarts += 1
                    delay = min(max_backoff, 2 ** min(c.restarts, 6))
                    c.next_start = now + delay
                    print(f"[launcher] {c.name} stopped (exit code {code}); restarting in {delay}s", flush=True)
                elif c.next_start and now >= c.next_start:
                    c.next_start = 0.0
                    c.start()
                    print(f"[launcher] {c.name} restarted", flush=True)
                state[c.name] = {"pid": c.proc.pid if c.proc else None, "alive": c.alive(), "restarts": c.restarts}
            try:
                conn.set(routing.LAUNCHER_STATE_KEY, json.dumps(state), ex=LAUNCHER_TTL_S)
            except Exception:
                pass
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping workers ...", flush=True)
    finally:
        for c in children:
            c.stop()
        try:
            for c in children:
                for q in queues_for(c.name):
                    conn.delete(routing.alive_key(q))      # leave no ghost marks behind
            conn.delete(routing.LAUNCHER_KEY, routing.LAUNCHER_STATE_KEY)
        except Exception:
            pass
    print("All workers stopped.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Start every DOCINT worker.")
    ap.add_argument("--only", help="comma list of pipelines (default: WORKER_PIPELINES, i.e. all installed)")
    ap.add_argument("--list", action="store_true", help="show what would start and exit")
    a = ap.parse_args(argv)
    if a.list:
        names, skipped = plan(a.only)
        print("would start:", ", ".join(names))
        for p, why in skipped:
            print(f"  skipped {p}: {why}")
        return 0
    return run(a.only)
