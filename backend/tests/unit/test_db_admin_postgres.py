"""Same browsing against a real PostgreSQL. Skipped unless DOCINT_TEST_PG_PORT is set (host/user/password/db via DOCINT_TEST_PG_*)."""
import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db_admin import browse

PORT = os.environ.get("DOCINT_TEST_PG_PORT")
pytestmark = pytest.mark.skipif(not PORT, reason="no PostgreSQL configured for tests")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app.main import create_app
    monkeypatch.setattr(settings, "DB_ADMIN_UI_ENABLED", True)
    monkeypatch.setattr(settings, "DB_ADMIN_ALLOWED_HOSTS", "testclient")
    monkeypatch.setattr(settings, "DB_ADMIN_DIR", str(tmp_path / "admin"))
    c = TestClient(create_app())
    yield c
    for sid in list(browse._sessions):
        browse.close_session(sid)


def test_postgres_browse(client):
    params = {"dialect": "postgresql", "host": os.environ.get("DOCINT_TEST_PG_HOST", "127.0.0.1"), "port": PORT,
              "database": os.environ.get("DOCINT_TEST_PG_DB", "demo"), "username": os.environ.get("DOCINT_TEST_PG_USER", "docint"),
              "password": os.environ.get("DOCINT_TEST_PG_PASSWORD", "")}
    r = client.post("/api/v1/db-admin/connect", json={"params": params})
    assert r.status_code == 200, r.text
    assert "PostgreSQL" in r.json()["data"]["server_version"]
    base = f"/api/v1/db-admin/sessions/{r.json()['data']['session_id']}"
    sch = client.get(base + "/schemas").json()["data"]
    assert "ops" in sch["schemas"] and "information_schema" not in sch["schemas"] and sch["default"] == "public"
    assert "information_schema" in client.get(base + "/schemas", params={"include_system": True}).json()["data"]["schemas"]
    tabs = {t["name"]: t for t in client.get(base + "/tables", params={"schema": "ops"}).json()["data"]}
    assert tabs["jobs"]["approx_rows"] == 40
    cols = client.get(base + "/columns", params={"schema": "ops", "table": "jobs"}).json()["data"]["columns"]
    assert [c["name"] for c in cols] == ["id", "payload", "at"] and cols[0]["primary_key"]
    rows = client.get(base + "/rows", params={"schema": "ops", "table": "jobs", "limit": 5, "order_by": "id", "desc": True}).json()["data"]
    assert [r[0] for r in rows["rows"]] == [40, 39, 38, 37, 36] and rows["has_more"] and "T" in rows["rows"][0][2]
    assert client.get(base + "/count", params={"schema": "ops", "table": "jobs"}).json()["data"]["count"] == 40
    # the session is read-only at the server: a write on the same connection is refused by PostgreSQL itself
    s = browse.get_session(base.rsplit("/", 1)[1])
    with s.engine.connect() as conn:
        with pytest.raises(Exception):
            conn.exec_driver_sql("delete from ops.jobs")
    bad = client.get(base + "/rows", params={"schema": "ops", "table": "jobs; drop table ops.jobs"})
    assert bad.status_code == 404
