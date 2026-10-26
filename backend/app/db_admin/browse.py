"""In-memory browse sessions and read-only inspection (schemas, tables, columns, rows, counts)."""
from __future__ import annotations

import datetime as dt
import decimal
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import column, func, inspect, select, table, text
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.exceptions import DocIntError
from app.db_admin.connect import ConnParams, humanize, make_engine, mask, probe

_ORACLE_SYSTEM = {
    "sys", "system", "outln", "dbsnmp", "appqossys", "audsys", "ctxsys", "dvsys", "dvf", "gsmadmin_internal", "lbacsys",
    "mdsys", "ojvmsys", "olapsys", "orddata", "ordsys", "ordplugins", "si_informtn_schema", "wmsys", "xdb", "xs$null",
    "remote_scheduler_agent", "sysbackup", "sysdg", "syskm", "sysrac", "gsmcatuser", "gsmuser", "ggsys", "dip", "anonymous",
    "oracle_ocm", "mddata", "flows_files", "apex_public_user", "ords_metadata", "ords_public_user", "pdbadmin",
}
_PG_SYSTEM = {"information_schema", "pg_catalog", "pg_toast"}


@dataclass
class Session:
    sid: str
    engine: Engine
    params: ConnParams
    label: str
    server_version: str | None
    created: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    connect_ms: int = 0


_sessions: dict[str, Session] = {}
_lock = threading.Lock()


def _purge() -> None:
    ttl = settings.DB_ADMIN_SESSION_TTL_S
    now = time.time()
    with _lock:
        for sid in [s for s, v in _sessions.items() if now - v.last_used > ttl]:
            _sessions.pop(sid).engine.dispose()


def open_session(p: ConnParams) -> Session:
    _purge()
    engine = None
    try:
        engine = make_engine(p, connect_timeout_s=settings.DB_ADMIN_CONNECT_TIMEOUT_S, query_timeout_s=settings.DB_ADMIN_QUERY_TIMEOUT_S)
        info = probe(engine, p)
    except DocIntError:
        raise
    except Exception as exc:  # noqa: BLE001
        if engine is not None:
            engine.dispose()
        raise DocIntError(f"Could not connect: {humanize(exc, p)}", "DB_CONNECT_FAILED") from None
    s = Session(sid=secrets.token_urlsafe(16), engine=engine, params=p, label=p.label(), server_version=info["server_version"], connect_ms=info["ms"])
    with _lock:
        _sessions[s.sid] = s
    return s


def get_session(sid: str) -> Session:
    _purge()
    with _lock:
        s = _sessions.get(sid)
        if s:
            s.last_used = time.time()
    if not s:
        raise DocIntError("This connection has expired or was closed. Connect again.", "DB_SESSION_NOT_FOUND")
    return s


def close_session(sid: str) -> bool:
    with _lock:
        s = _sessions.pop(sid, None)
    if s:
        s.engine.dispose()
    return s is not None


def _run(s: Session, fn):
    try:
        return fn()
    except DocIntError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocIntError(f"The database refused that: {humanize(exc, s.params)}", "DB_QUERY_FAILED") from None


def schemas(s: Session, include_system: bool = False) -> dict[str, Any]:
    def go():
        insp = inspect(s.engine)
        names = insp.get_schema_names()
        default = insp.default_schema_name
        if s.params.dialect == "sqlite":
            return {"schemas": ["main"], "default": "main"}
        if not include_system:
            sysset = _ORACLE_SYSTEM if s.params.dialect == "oracle" else _PG_SYSTEM
            names = [n for n in names if n.lower() not in sysset and not n.lower().startswith("pg_temp") and not n.lower().startswith("pg_toast_temp")]
        names = sorted(set(names), key=lambda n: (n != default, n))
        return {"schemas": names, "default": default}
    return _run(s, go)


def _schema_arg(s: Session, schema: str | None) -> str | None:
    return None if (s.params.dialect == "sqlite" or not schema) else schema


def tables(s: Session, schema: str | None) -> list[dict[str, Any]]:
    def go():
        sch = _schema_arg(s, schema)
        insp = inspect(s.engine)
        base = [(n, "table") for n in insp.get_table_names(schema=sch)]
        try:
            base += [(n, "view") for n in insp.get_view_names(schema=sch)]
        except Exception:
            pass
        approx: dict[str, int | None] = {}
        try:
            with s.engine.connect() as conn:
                if s.params.dialect == "oracle":
                    owner = s.engine.dialect.denormalize_name(sch or insp.default_schema_name)
                    for name, n in conn.execute(text("SELECT table_name, num_rows FROM all_tables WHERE owner = :o"), {"o": owner}):
                        approx[s.engine.dialect.normalize_name(name)] = int(n) if n is not None else None
                elif s.params.dialect == "postgresql":
                    q = text("SELECT c.relname, c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s")
                    for name, n in conn.execute(q, {"s": sch or insp.default_schema_name}):
                        approx[name] = int(n) if n is not None and n >= 0 else None
        except Exception:
            approx = {}
        return [{"name": n, "kind": k, "approx_rows": approx.get(n)} for n, k in sorted(base)]
    return _run(s, go)


def _require_table(s: Session, schema: str | None, name: str):
    """Only names the database itself lists are ever put into a query."""
    sch = _schema_arg(s, schema)
    insp = inspect(s.engine)
    known = set(insp.get_table_names(schema=sch))
    try:
        known |= set(insp.get_view_names(schema=sch))
    except Exception:
        pass
    if name not in known:
        raise DocIntError(f"No table or view named '{name}' in this schema.", "DB_OBJECT_NOT_FOUND")
    return sch, insp


def columns(s: Session, schema: str | None, name: str) -> dict[str, Any]:
    def go():
        sch, insp = _require_table(s, schema, name)
        pk = set((insp.get_pk_constraint(name, schema=sch) or {}).get("constrained_columns") or [])
        cols = [{"name": c["name"], "type": str(c["type"]), "nullable": bool(c.get("nullable", True)),
                 "primary_key": c["name"] in pk, "default": None if c.get("default") is None else str(c["default"])[:80]}
                for c in insp.get_columns(name, schema=sch)]
        fks = []
        try:
            for fk in insp.get_foreign_keys(name, schema=sch):
                fks.append({"columns": fk.get("constrained_columns"), "ref_schema": fk.get("referred_schema"),
                            "ref_table": fk.get("referred_table"), "ref_columns": fk.get("referred_columns")})
        except Exception:
            pass
        return {"columns": cols, "foreign_keys": fks}
    return _run(s, go)


def _lite(s: Session, schema: str | None, name: str):
    sch, insp = _require_table(s, schema, name)
    col_names = [c["name"] for c in insp.get_columns(name, schema=sch)]
    return table(name, *[column(c) for c in col_names], schema=sch), col_names


def jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else str(v)
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() and abs(v) < 10**15 else float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    if hasattr(v, "read"):                       # Oracle LOB
        try:
            v = v.read()
        except Exception:
            return "<unreadable LOB>"
        return jsonable(v)
    text_v = str(v)
    return text_v if len(text_v) <= 2000 else text_v[:2000] + f"… (+{len(text_v) - 2000} chars)"


def rows(s: Session, schema: str | None, name: str, limit: int, offset: int, order_by: str | None, desc: bool) -> dict[str, Any]:
    limit = max(1, min(int(limit), settings.DB_ADMIN_MAX_ROWS))
    offset = max(0, int(offset))

    def go():
        t, col_names = _lite(s, schema, name)
        q = select(t)
        if order_by:
            if order_by not in col_names:
                raise DocIntError(f"No column named '{order_by}'.", "DB_OBJECT_NOT_FOUND")
            c = t.c[order_by]
            q = q.order_by(c.desc() if desc else c.asc())
        q = q.limit(limit + 1).offset(offset)
        with s.engine.connect() as conn:
            got = conn.execute(q).fetchall()
        more = len(got) > limit
        return {"columns": col_names, "rows": [[jsonable(v) for v in r] for r in got[:limit]],
                "offset": offset, "limit": limit, "has_more": more}
    return _run(s, go)


def count(s: Session, schema: str | None, name: str) -> int:
    def go():
        t, _ = _lite(s, schema, name)
        with s.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(t)).scalar() or 0)
    return _run(s, go)
