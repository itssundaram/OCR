"""Database page: switches, read-only browsing on SQLite, saved profiles, override file, URL building."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db_admin import browse, vault
from app.db_admin.connect import ConnParams, build_url, mask, url_string


@pytest.fixture()
def sqlite_file(tmp_path):
    p = tmp_path / "demo.db"
    con = sqlite3.connect(p)
    con.executescript("""
      create table departments (id integer primary key, slug text not null, note blob);
      create table jobs (id integer primary key, dept_id integer references departments(id), payload text, score real);
      create view v_jobs as select id, payload from jobs;
    """)
    con.executemany("insert into departments(slug, note) values (?, ?)", [("TMS", b"\x00\x01"), ("MMS", None)])
    con.executemany("insert into jobs(dept_id, payload, score) values (?, ?, ?)", [(1 + i % 2, "x" * (i + 1), i / 3) for i in range(30)])
    con.commit(); con.close()
    return str(p)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app.main import create_app
    monkeypatch.setattr(settings, "DB_ADMIN_UI_ENABLED", True)
    monkeypatch.setattr(settings, "DB_ADMIN_ALLOWED_HOSTS", "testclient")
    monkeypatch.setattr(settings, "DB_ADMIN_DIR", str(tmp_path / "admin"))
    monkeypatch.setattr(settings, "DB_ADMIN_MAX_ROWS", 10)
    c = TestClient(create_app())
    yield c
    for sid in list(browse._sessions):
        browse.close_session(sid)


def _connect(client, path):
    r = client.post("/api/v1/db-admin/connect", json={"params": {"dialect": "sqlite", "path": path}})
    assert r.status_code == 200, r.text
    return r.json()["data"]["session_id"]


def test_off_by_default_and_host_check(client, monkeypatch, sqlite_file):
    monkeypatch.setattr(settings, "DB_ADMIN_UI_ENABLED", False)
    assert client.get("/api/v1/db-admin/status").json()["data"] == {"enabled": False, "allowed": False, "client": "testclient"}
    r = client.post("/api/v1/db-admin/connect", json={"params": {"dialect": "sqlite", "path": sqlite_file}})
    assert r.status_code == 403 and r.json()["error"]["code"] == "DB_ADMIN_DISABLED"
    monkeypatch.setattr(settings, "DB_ADMIN_UI_ENABLED", True)
    monkeypatch.setattr(settings, "DB_ADMIN_ALLOWED_HOSTS", "10.1.2.3")
    r = client.get("/api/v1/db-admin/profiles")
    assert r.status_code == 403 and r.json()["error"]["code"] == "DB_ADMIN_FORBIDDEN_HOST"
    monkeypatch.setattr(settings, "DB_ADMIN_ALLOWED_HOSTS", "testclient")
    assert client.get("/api/v1/db-admin/profiles").status_code == 200


def test_browse_schemas_tables_columns_rows_count(client, sqlite_file):
    sid = _connect(client, sqlite_file)
    base = f"/api/v1/db-admin/sessions/{sid}"
    assert client.get(base + "/schemas").json()["data"]["schemas"] == ["main"]
    tabs = {t["name"]: t["kind"] for t in client.get(base + "/tables").json()["data"]}
    assert tabs == {"departments": "table", "jobs": "table", "v_jobs": "view"}
    cols = client.get(base + "/columns", params={"table": "jobs"}).json()["data"]
    assert [c["name"] for c in cols["columns"]] == ["id", "dept_id", "payload", "score"]
    assert cols["columns"][0]["primary_key"] is True
    assert cols["foreign_keys"][0]["ref_table"] == "departments"
    page1 = client.get(base + "/rows", params={"table": "jobs", "limit": 5}).json()["data"]
    assert len(page1["rows"]) == 5 and page1["has_more"] is True
    page_last = client.get(base + "/rows", params={"table": "jobs", "limit": 10, "offset": 25}).json()["data"]
    assert len(page_last["rows"]) == 5 and page_last["has_more"] is False
    capped = client.get(base + "/rows", params={"table": "jobs", "limit": 500}).json()["data"]
    assert capped["limit"] == 10 and len(capped["rows"]) == 10                 # DB_ADMIN_MAX_ROWS
    desc = client.get(base + "/rows", params={"table": "jobs", "limit": 3, "order_by": "id", "desc": True}).json()["data"]
    assert [r[0] for r in desc["rows"]] == [30, 29, 28]
    assert client.get(base + "/count", params={"table": "jobs"}).json()["data"]["count"] == 30
    blob = client.get(base + "/rows", params={"table": "departments"}).json()["data"]
    assert blob["rows"][0][2] == "<2 bytes>"


def test_only_listed_names_reach_a_query(client, sqlite_file):
    sid = _connect(client, sqlite_file)
    base = f"/api/v1/db-admin/sessions/{sid}"
    for bad in ("jobs; drop table jobs", "jobs--", "sqlite_master", "JOBS", '"jobs"'):
        r = client.get(base + "/rows", params={"table": bad})
        assert r.status_code == 404 and r.json()["error"]["code"] == "DB_OBJECT_NOT_FOUND", bad
    r = client.get(base + "/rows", params={"table": "jobs", "order_by": "id; delete from jobs"})
    assert r.status_code == 404
    assert client.get(base + "/count", params={"table": "jobs"}).json()["data"]["count"] == 30   # still there


def test_connection_is_read_only(client, sqlite_file):
    sid = _connect(client, sqlite_file)
    s = browse.get_session(sid)
    with s.engine.connect() as conn:
        with pytest.raises(Exception):
            conn.exec_driver_sql("delete from jobs")


def test_bad_connect_and_expired_session(client, tmp_path):
    r = client.post("/api/v1/db-admin/connect", json={"params": {"dialect": "sqlite", "path": str(tmp_path / "nope.db")}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "DB_CONNECT_FAILED"
    r = client.post("/api/v1/db-admin/connect", json={"params": {"dialect": "mysql"}})
    assert r.status_code == 400
    r = client.get("/api/v1/db-admin/sessions/nosuch/tables")
    assert r.status_code == 404 and r.json()["error"]["code"] == "DB_SESSION_NOT_FOUND"


def test_oracle_failure_message_hides_password(client):
    r = client.post("/api/v1/db-admin/connect", json={"params": {
        "dialect": "oracle", "host": "127.0.0.1", "port": 9, "service_name": "X", "username": "u", "password": "S3cret!pw"}})
    assert r.status_code == 400
    assert "S3cret!pw" not in r.text


def test_profiles_encrypt_password_and_never_return_it(client, sqlite_file, tmp_path):
    r = client.post("/api/v1/db-admin/profiles", json={"name": "demo (local)", "params": {"dialect": "oracle", "host": "h", "service_name": "s",
                                                                                          "username": "u", "password": "P@ss:w/rd#1"}})
    assert r.status_code == 200, r.text
    listed = client.get("/api/v1/db-admin/profiles").json()["data"]
    assert listed[0]["has_password"] is True and "P@ss:w/rd#1" not in json.dumps(listed) and "password_enc" not in json.dumps(listed)
    raw = (tmp_path / "admin" / "profiles.json").read_text()
    assert "P@ss:w/rd#1" not in raw
    assert vault.profile_params(listed[0]["id"])["password"] == "P@ss:w/rd#1"
    assert client.delete(f"/api/v1/db-admin/profiles/{listed[0]['id']}").status_code == 200
    assert client.get("/api/v1/db-admin/profiles").json()["data"] == []


def test_connect_by_profile_and_save_as(client, sqlite_file):
    r = client.post("/api/v1/db-admin/connect", json={"params": {"dialect": "sqlite", "path": sqlite_file}, "save_as": "local sqlite"})
    assert r.status_code == 200
    pid = client.get("/api/v1/db-admin/profiles").json()["data"][0]["id"]
    r = client.post("/api/v1/db-admin/connect", json={"profile_id": pid})
    assert r.status_code == 200 and r.json()["data"]["dialect"] == "sqlite"


def test_use_as_app_db_override_roundtrip(client, sqlite_file, monkeypatch):
    sid = _connect(client, sqlite_file)
    r = client.post(f"/api/v1/db-admin/sessions/{sid}/use-as-app-db")
    assert r.status_code == 200 and r.json()["data"]["restart_required"] is True
    assert client.get("/api/v1/db-admin/status").json()["data"]["override"]["label"].startswith("sqlite:")
    assert sqlite_file in vault.read_override_url()
    assert sqlite_file not in vault.override_path().read_text()                # stored encrypted
    monkeypatch.setattr(settings, "DATABASE_URL", None)
    assert vault.apply_override(settings) is True and settings.DATABASE_URL.endswith(sqlite_file)
    assert client.delete("/api/v1/db-admin/override").json()["data"]["cleared"] is True
    assert vault.read_override_url() is None


def test_urls_survive_special_characters():
    p = ConnParams.from_dict({"dialect": "oracle", "host": "db.lan", "port": "1522", "service_name": "FREEPDB1", "username": "docint", "password": "p@ss:w/rd#?"})
    u = make_url(url_string(p))
    assert (u.host, u.port, u.username, u.password, u.query["service_name"]) == ("db.lan", 1522, "docint", "p@ss:w/rd#?", "FREEPDB1")
    assert "p@ss" not in str(build_url(p))                                       # str() of a URL hides the password
    assert mask("failed for postgresql://u:hunter2@h/db with hunter2", "hunter2") == "failed for postgresql://u:***@h/db with ***"


def test_app_database_url_escapes_password(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", None)
    monkeypatch.setattr(settings, "ORACLE_PASSWORD", "a@b:c/d")
    u = make_url(settings.database_url)
    assert u.password == "a@b:c/d" and u.host == settings.ORACLE_HOST
