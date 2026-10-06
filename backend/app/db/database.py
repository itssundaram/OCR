"""
DOCINT — Database Engine & Session Factory
Oracle 23ai Free via SQLAlchemy + python-oracledb Thin mode.
No Oracle Instant Client required.
"""
from __future__ import annotations

import json
from typing import Generator, Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator, Text

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class OracleJSON(TypeDecorator):
    """
    A JSON column type compatible with Oracle 23ai + python-oracledb thin mode.
    Stores JSON as CLOB/TEXT; serializes to string on write, deserializes on read.
    This avoids the '_json_serializer' AttributeError from the raw SQLAlchemy JSON type
    when using the oracledb thin driver.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return json.dumps(value)

    def process_result_value(self, value: Any, dialect) -> Any:
        if value is None:
            return None
        # python-oracledb (thin mode) on Oracle 21c+ automatically deserializes
        # JSON-annotated CLOB columns into Python dicts/lists before SQLAlchemy
        # sees them. If that already happened, return as-is.
        if isinstance(value, (dict, list)):
            return value
        # LOB object (older driver behaviour) — read it into a string first
        if hasattr(value, "read"):
            value = value.read()
        return json.loads(value)


class Base(DeclarativeBase):
    """All SQLAlchemy ORM models inherit from this class."""
    pass


def _build_engine():
    safe_url = (
        f"oracle+oracledb://{settings.ORACLE_USER}:***"
        f"@{settings.ORACLE_HOST}:{settings.ORACLE_PORT}"
        f"/?service_name={settings.ORACLE_SERVICE_NAME}"
    )
    logger.info("database_engine_creating", url=safe_url)

    engine = create_engine(
        settings.database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=False,  # Disabled to remove noisy terminal logs
        future=True,
    )
    logger.info("database_engine_created")
    return engine


engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency providing a SQLAlchemy Session.
    Usage: def route(db: Session = Depends(get_db))
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_oracle_connectivity() -> dict:
    """Lightweight DB health check for /health endpoint.
    Dialect-aware: Oracle requires `SELECT 1 FROM DUAL` (no bare SELECT
    without FROM), Postgres/others accept a bare `SELECT 1`. Kept portable
    since this app runs against either backend depending on deployment."""
    try:
        with engine.connect() as conn:
            probe = "SELECT 1 FROM DUAL" if engine.dialect.name == "oracle" else "SELECT 1"
            conn.execute(text(probe)).fetchone()
        return {"status": "ok", "message": "Database connection successful"}
    except Exception as exc:
        logger.error("database_health_check_failed", error=str(exc))
        return {"status": "error", "message": str(exc)}
