"""
DOCINT — Structured Logging Setup
Uses structlog for structured, JSON-friendly log output.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """Configure structlog and stdlib logging. Called once at application startup."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.APP_ENV == "development":
        processors = shared_processors + [structlog.dev.ConsoleRenderer(colors=True)]
    else:
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.BoundLogger:
    """Return a named structlog logger."""
    return structlog.get_logger(name)


def bind_job_context(job_id: str, document_type: str | None = None) -> None:
    """Bind job_id to the current thread's log context."""
    structlog.contextvars.bind_contextvars(
        job_id=job_id,
        **({"document_type": document_type} if document_type else {}),
    )


def clear_job_context() -> None:
    """Clear bound context vars at the end of a request/job."""
    structlog.contextvars.clear_contextvars()
