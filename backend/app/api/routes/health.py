"""
DOCINT — Health Check Route
GET /api/v1/health
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from redis import Redis

from app.core.config import settings
from app.core.logging import get_logger
from app.api.dependencies import get_db, get_redis_connection
from app.services.health_service import HealthService

router = APIRouter(tags=["Health"])
logger = get_logger(__name__)


def _check_storage() -> dict:
    try:
        path = settings.storage_path
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".health_check"
        test_file.write_text("ok")
        test_file.unlink()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/health")
def health_check(
    db: Session = Depends(get_db),
    redis_conn: Redis = Depends(get_redis_connection)
):
    """Full system health check."""
    health_service = HealthService(db_session=db, redis_conn=redis_conn)
    oracle = health_service.check_oracle()
    redis = health_service.check_redis()
    workers = health_service.check_workers()
    ai = health_service.check_ollama()
    storage = _check_storage()

    oracle_ok = oracle.get("status") == "ok"
    overall = "healthy" if oracle_ok else "degraded"

    payload = {
        "status": overall,
        "version": settings.APP_VERSION,
        "environment": settings.APP_ENV,
        "ai_provider": settings.AI_PROVIDER,
        "ai_model": settings.AI_MODEL,
        "components": {
            "api": "ok",
            "oracle": oracle,
            "redis": redis,
            # Phase 10: informational only (not gated into `overall` below,
            # to avoid changing existing monitoring/alerting behavior on
            # this endpoint) — "redis" proves Redis is reachable, "workers"
            # proves something is actually consuming the queue.
            "workers": workers,
            "ai_model": ai,
            "storage": storage,
        },
    }
    logger.info("health_check", status=overall)
    return JSONResponse(content=payload, status_code=200 if oracle_ok else 503)
