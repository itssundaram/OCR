"""
DOCINT — Startup Utilities
Pre-loads and warms up AI/OCR models so the first request doesn't suffer a cold start.
"""
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def preload_models() -> None:
    """
    Models are now loaded lazily by the Redis workers. 
    The API server no longer needs to preload them.
    """
    logger.info("model_preload_skipped", reason="processing_handled_by_redis_workers")
