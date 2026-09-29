"""
DOCINT — Health Check Route
GET /api/v1/health
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import get_logger
from app.db.database import check_oracle_connectivity

router = APIRouter(tags=["Health"])
logger = get_logger(__name__)


def _check_redis() -> dict:
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.REDIS_URL, socket_timeout=2)
        r.ping()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def _check_ollama() -> dict:
    if settings.AI_PROVIDER != "ollama":
        return {"status": "not_configured"}
    try:
        import httpx
        response = httpx.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=3.0)
        if response.status_code == 200:
            models = response.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            model_present = any(
                settings.AI_MODEL in n or n.startswith(settings.AI_MODEL.split(":")[0])
                for n in model_names
            )
            return {"status": "ok", "model": settings.AI_MODEL, "model_available": model_present}
        return {"status": "error", "message": f"HTTP {response.status_code}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


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
def health_check():
    """Full system health check."""
    oracle = check_oracle_connectivity()
    redis = _check_redis()
    ai = _check_ollama()
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
            "ai_model": ai,
            "storage": storage,
        },
    }
    logger.info("health_check", status=overall)
    return JSONResponse(content=payload, status_code=200 if oracle_ok else 503)
