"""
Database page API: connect with typed credentials, browse schemas / tables / columns / rows (read-only).

Everything here is OFF unless DB_ADMIN_UI_ENABLED=true, and answers only to this machine unless
DB_ADMIN_ALLOWED_HOSTS lists other client addresses. DOCINT has no login, so these two switches are the lock.
Only SELECT statements built from names the database itself listed are ever run; there is no free-form SQL.
"""
from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.core.config import settings
from app.core.exceptions import DocIntError
from app.core.logging import get_logger
from app.db_admin import browse, vault
from app.db_admin.connect import ConnParams, mask, url_string
from app.schemas.common import APIResponse

logger = get_logger(__name__)
router = APIRouter(tags=["Database page"], prefix="/db-admin")

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _client_allowed(host: str | None) -> bool:
    if not host:
        return False
    if host in _LOOPBACK or host.startswith("::ffff:127."):
        return True
    for entry in [e.strip() for e in settings.DB_ADMIN_ALLOWED_HOSTS.split(",") if e.strip()]:
        if entry == host:
            return True
        try:
            if ipaddress.ip_address(host) in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def guard(request: Request) -> None:
    if not settings.DB_ADMIN_UI_ENABLED:
        raise DocIntError("The Database page is switched off. Set DB_ADMIN_UI_ENABLED=true in .env and restart the API.", "DB_ADMIN_DISABLED")
    host = request.client.host if request.client else None
    if not _client_allowed(host):
        raise DocIntError(f"This address ({host}) may not use the Database page. Add it to DB_ADMIN_ALLOWED_HOSTS in .env.", "DB_ADMIN_FORBIDDEN_HOST")


class ConnectRequest(BaseModel):
    profile_id: str | None = None
    params: dict[str, Any] | None = None
    save_as: str | None = None          # also save these params (and password) under this name


class ProfileRequest(BaseModel):
    name: str
    params: dict[str, Any]
    profile_id: str | None = None


def _app_db_summary() -> dict[str, Any]:
    from sqlalchemy.engine import make_url
    try:
        u = make_url(settings.database_url)
        return {"dialect": u.get_backend_name(), "host": u.host, "port": u.port, "database": u.database or u.query.get("service_name"),
                "user": u.username}
    except Exception:
        return {"dialect": None}


@router.get("/status", response_model=APIResponse[dict])
def status(request: Request):
    """Always answers: tells the page whether it may be used and what the app itself is connected to."""
    enabled = bool(settings.DB_ADMIN_UI_ENABLED)
    host = request.client.host if request.client else None
    data: dict[str, Any] = {"enabled": enabled, "allowed": enabled and _client_allowed(host), "client": host}
    if data["allowed"]:
        import importlib.util
        data.update({
            "app_database": _app_db_summary(),
            "override": vault.override_info(),
            "max_rows": settings.DB_ADMIN_MAX_ROWS,
            "can_store_passwords": bool(importlib.util.find_spec("cryptography")),
            "drivers": {"oracle": bool(importlib.util.find_spec("oracledb")),
                        "postgresql": bool(importlib.util.find_spec("psycopg2") or importlib.util.find_spec("psycopg")),
                        "sqlite": True},
        })
    return APIResponse(data=data)


@router.get("/profiles", response_model=APIResponse[list], dependencies=[Depends(guard)])
def list_profiles():
    return APIResponse(data=vault.list_profiles())


@router.post("/profiles", response_model=APIResponse[dict], dependencies=[Depends(guard)])
def save_profile(req: ProfileRequest):
    password = (req.params or {}).get("password") or None
    p = ConnParams.from_dict(req.params)
    return APIResponse(data=vault.save_profile(req.name, p.public(), password, req.profile_id))


@router.delete("/profiles/{profile_id}", response_model=APIResponse[dict], dependencies=[Depends(guard)])
def delete_profile(profile_id: str):
    if not vault.delete_profile(profile_id):
        raise DocIntError("That saved connection no longer exists.", "DB_PROFILE_NOT_FOUND")
    return APIResponse(data={"deleted": True})


@router.post("/connect", response_model=APIResponse[dict], dependencies=[Depends(guard)])
def connect(req: ConnectRequest):
    """Open a browse connection. The password is sent once here and kept only in server memory for this session."""
    if req.profile_id:
        raw = vault.profile_params(req.profile_id)
        if req.params:                                   # typed values override the saved ones (password only if typed)
            raw.update({k: v for k, v in req.params.items() if not (k == "password" and not v)})
    else:
        raw = dict(req.params or {})
    p = ConnParams.from_dict(raw)
    s = browse.open_session(p)
    if req.save_as:
        vault.save_profile(req.save_as, p.public(), p.password or None, req.profile_id)
    logger.info("db_admin_connected", target=mask(p.label(), p.password))
    return APIResponse(data={"session_id": s.sid, "label": s.label, "server_version": s.server_version,
                             "connect_ms": s.connect_ms, "dialect": p.dialect})


@router.post("/sessions/{sid}/disconnect", response_model=APIResponse[dict], dependencies=[Depends(guard)])
def disconnect(sid: str):
    return APIResponse(data={"closed": browse.close_session(sid)})


@router.get("/sessions/{sid}/schemas", response_model=APIResponse[dict], dependencies=[Depends(guard)])
def schemas(sid: str, include_system: bool = False):
    return APIResponse(data=browse.schemas(browse.get_session(sid), include_system))


@router.get("/sessions/{sid}/tables", response_model=APIResponse[list], dependencies=[Depends(guard)])
def tables(sid: str, schema: str | None = None):
    return APIResponse(data=browse.tables(browse.get_session(sid), schema))


@router.get("/sessions/{sid}/columns", response_model=APIResponse[dict], dependencies=[Depends(guard)])
def columns(sid: str, table: str, schema: str | None = None):
    return APIResponse(data=browse.columns(browse.get_session(sid), schema, table))


@router.get("/sessions/{sid}/rows", response_model=APIResponse[dict], dependencies=[Depends(guard)])
def rows(sid: str, table: str, schema: str | None = None, limit: int = Query(200, ge=1), offset: int = Query(0, ge=0),
         order_by: str | None = None, desc: bool = False):
    return APIResponse(data=browse.rows(browse.get_session(sid), schema, table, limit, offset, order_by, desc))


@router.get("/sessions/{sid}/count", response_model=APIResponse[dict], dependencies=[Depends(guard)])
def count(sid: str, table: str, schema: str | None = None):
    return APIResponse(data={"count": browse.count(browse.get_session(sid), schema, table)})


@router.post("/sessions/{sid}/use-as-app-db", response_model=APIResponse[dict], dependencies=[Depends(guard)])
def use_as_app_db(sid: str):
    """Save this connection as the one the app itself uses. Takes effect after the API and workers restart."""
    s = browse.get_session(sid)
    if s.params.dialect == "sqlite" and not s.params.path:
        raise DocIntError("Nothing to save.", "DB_CONNECT_FAILED")
    vault.write_override(url_string(s.params), s.label)
    logger.warning("db_admin_app_database_override_written", target=mask(s.label, s.params.password))
    return APIResponse(data={"saved": True, "label": s.label, "restart_required": True,
                             "note": "Restart the API and run_worker.py. The first start creates no tables: the database must already hold the DOCINT schema."})


@router.delete("/override", response_model=APIResponse[dict], dependencies=[Depends(guard)])
def clear_override():
    return APIResponse(data={"cleared": vault.clear_override(), "restart_required": True})
