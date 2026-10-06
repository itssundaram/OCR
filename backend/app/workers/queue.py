"""
DOCINT — RQ Queue Singleton
Single source of truth for the Redis connection and RQ queue.
Both the API (enqueue) and worker startup reference this module.

Redis URL and queue name are read from settings (.env) — nothing hardcoded here.
"""
from __future__ import annotations

import redis
from rq import Queue

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    redis_conn = redis.from_url(settings.REDIS_URL, socket_connect_timeout=3)
    redis_conn.ping()
    logger.info("redis_connected", url=settings.REDIS_URL)
except Exception as exc:
    logger.warning("redis_unavailable", error=str(exc))
    redis_conn = None  # type: ignore[assignment]

# Queue instance — callers should check redis_conn is not None before using
q = Queue(
    name=settings.REDIS_QUEUE_NAME,
    connection=redis_conn,  # type: ignore[arg-type]
    default_timeout=settings.JOB_TIMEOUT_SECONDS,
) if redis_conn else None
