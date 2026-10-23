"""
Live events (envelope v1).

Each job has one Redis Stream, `docint:job:<id>:events`. The worker appends,
the API tails it for SSE. Stream ids are "<seq>-0", so the SSE `id:` is the seq
and a browser's Last-Event-ID maps straight back to a stream position.

Publishing never breaks a job: if Redis is down or slow the event is dropped
and the job carries on (results are in Oracle either way).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = 1
TERMINAL_TYPES = ("job_completed", "job_failed", "refine_completed")


def is_terminal(env: dict) -> bool:
    """job_completed ends the stream unless background refinement is still going to speak."""
    t = env.get("type")
    if t == "job_completed":
        return not (env.get("data") or {}).get("refine")
    return t in TERMINAL_TYPES
# These also go to the Oracle event log (Logs page); per-field events do not.
COARSE_TYPES = {
    "job_started", "page_completed", "fallback_started", "field_failed",
    "table_extracted", "job_completed", "job_failed",
}


def stream_key(job_id: str) -> str:
    return f"docint:job:{job_id}:events"


def cancel_key(job_id: str) -> str:
    return f"docint:job:{job_id}:cancel"


def _redis():
    try:
        from app.workers.queue import redis_conn
        return redis_conn
    except Exception:
        return None


class EventBus:
    def __init__(self, job_id: str, coarse_callback: Callable[[str, dict], None] | None = None, redis_conn=None, seq_start: int = 0):
        self.job_id = job_id
        self._coarse = coarse_callback
        self._redis = redis_conn if redis_conn is not None else _redis()
        self._lock = threading.Lock()
        self._seq = seq_start
        self._warned = False
        self._cancel_checked = 0.0
        self._cancelled = False
        self.history: list[dict] = []     # in-memory copy (tests, final summary)

    def emit(self, type_: str, page: int | None = None, **data) -> dict:
        with self._lock:
            self._seq += 1
            env = {
                "v": SCHEMA_VERSION, "job_id": self.job_id, "seq": self._seq,
                "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "type": type_, "page": page, "data": data,
            }
            self.history.append(env)
            self._publish(env)
        if self._coarse and type_ in COARSE_TYPES:
            try:
                self._coarse(f"FAST_{type_.upper()}", {"page": page, **data})
            except Exception as exc:  # DB log problems must never fail a job
                logger.warning("fast_event_db_log_failed", error=str(exc))
        return env

    def _publish(self, env: dict) -> None:
        if self._redis is None:
            return
        try:
            key = stream_key(self.job_id)
            pipe = self._redis.pipeline()
            pipe.xadd(key, {"e": json.dumps(env, separators=(",", ":"), default=str)},
                      id=f"{env['seq']}-0", maxlen=settings.FAST_EVENT_STREAM_MAXLEN, approximate=True)
            pipe.expire(key, settings.FAST_EVENT_STREAM_TTL_S)
            pipe.execute()
        except Exception as exc:
            if not self._warned:
                self._warned = True
                logger.warning("fast_event_publish_failed", error=str(exc))

    def last_seq(self) -> int:
        """Highest seq already on the stream (a refine job continues numbering after the extraction job)."""
        if self._redis is None:
            return 0
        try:
            last = self._redis.xrevrange(stream_key(self.job_id), count=1)
            return int(last[0][0].decode().split("-")[0]) if last else 0
        except Exception:
            return 0

    def cancel_requested(self) -> bool:
        if self._cancelled:
            return True
        now = time.monotonic()
        if self._redis is None or now - self._cancel_checked < 1.0:
            return False
        self._cancel_checked = now
        try:
            self._cancelled = bool(self._redis.exists(cancel_key(self.job_id)))
        except Exception:
            pass
        return self._cancelled
