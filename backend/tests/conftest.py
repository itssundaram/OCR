"""
DOCINT — Shared Test Fixtures
Uses the real Oracle 23ai Free database (from .env config).
No SQLite. No in-memory databases.
"""
import pytest
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.db.database import engine, Base, get_db
from app.main import app


# ── Session Factory bound to the real Oracle engine ──────────────────────────
TestingSessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


@pytest.fixture(scope="function")
def db_session():
    """
    Provides a real Oracle DB session for each test.
    All changes are rolled back after the test to keep the DB clean.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def client(db_session):
    """
    FastAPI TestClient with get_db overridden to use the Oracle test session.
    """
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
