"""Connection parameters -> SQLAlchemy URL / engine, with timeouts, read-only settings and masked errors."""
from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.pool import NullPool

from app.core.exceptions import DocIntError

DIALECTS = ("oracle", "postgresql", "sqlite")


@dataclass
class ConnParams:
    dialect: str = "oracle"
    host: str = ""
    port: int | None = None
    service_name: str = ""     # Oracle
    sid: str = ""              # Oracle (older style); used when no service name is given
    database: str = ""         # PostgreSQL
    username: str = ""
    password: str = ""
    path: str = ""             # SQLite file

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("password", None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ConnParams":
        d = d or {}
        known = {k: d.get(k) for k in cls.__dataclass_fields__ if d.get(k) is not None}
        if known.get("port") in ("", None):
            known.pop("port", None)
        elif not isinstance(known["port"], int):
            try:
                known["port"] = int(known["port"])
            except (TypeError, ValueError):
                raise DocIntError("Port must be a number.", "DB_CONNECT_FAILED")
        for k in ("host", "service_name", "sid", "database", "username", "path", "dialect"):
            if k in known:
                known[k] = str(known[k]).strip()
        p = cls(**known)
        if p.dialect not in DIALECTS:
            raise DocIntError(f"Unsupported database type '{p.dialect}'. Use one of: {', '.join(DIALECTS)}.", "DB_CONNECT_FAILED")
        return p

    def label(self) -> str:
        if self.dialect == "sqlite":
            return f"sqlite:{os.path.basename(self.path)}"
        target = self.service_name or self.sid or self.database
        return f"{self.dialect}:{self.username}@{self.host}:{self.port or ''}/{target}"


def mask(message: str, *secrets: str) -> str:
    """Remove passwords from any text before it is logged or shown."""
    out = str(message)
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return re.sub(r"(://[^:/@\s]+:)[^@\s]+@", r"\1***@", out)


def _pg_driver() -> str:
    if importlib.util.find_spec("psycopg2"):
        return "postgresql+psycopg2"
    if importlib.util.find_spec("psycopg"):
        return "postgresql+psycopg"
    raise DocIntError("PostgreSQL support needs the 'psycopg2-binary' package, which is not installed on this server.", "DB_DRIVER_MISSING")


def build_url(p: ConnParams) -> URL:
    if p.dialect == "oracle":
        if not (p.host and p.username and (p.service_name or p.sid)):
            raise DocIntError("Oracle needs host, user and a service name (or SID).", "DB_CONNECT_FAILED")
        query = {"service_name": p.service_name} if p.service_name else {}
        return URL.create("oracle+oracledb", username=p.username, password=p.password or None, host=p.host,
                          port=p.port or 1521, database=None if p.service_name else p.sid, query=query)
    if p.dialect == "postgresql":
        if not (p.host and p.username and p.database):
            raise DocIntError("PostgreSQL needs host, database and user.", "DB_CONNECT_FAILED")
        return URL.create(_pg_driver(), username=p.username, password=p.password or None, host=p.host,
                          port=p.port or 5432, database=p.database)
    if not p.path:
        raise DocIntError("SQLite needs the path of the database file.", "DB_CONNECT_FAILED")
    if not os.path.isfile(p.path):
        raise DocIntError(f"No SQLite file at {p.path}.", "DB_CONNECT_FAILED")
    return URL.create("sqlite+pysqlite", database=p.path)


def url_string(p: ConnParams) -> str:
    """Full URL with the password in it (only for the override file; never log it)."""
    return build_url(p).render_as_string(hide_password=False)


def make_engine(p: ConnParams, *, connect_timeout_s: int, query_timeout_s: int) -> Engine:
    url = build_url(p)
    args: dict[str, Any] = {}
    if p.dialect == "oracle":
        args["tcp_connect_timeout"] = float(connect_timeout_s)
    elif p.dialect == "postgresql":
        args["connect_timeout"] = connect_timeout_s
        args["options"] = f"-c default_transaction_read_only=on -c statement_timeout={query_timeout_s * 1000}"
    eng = create_engine(url, poolclass=NullPool, connect_args=args, future=True)

    from sqlalchemy import event

    if p.dialect == "oracle":
        @event.listens_for(eng, "connect")
        def _oracle_timeout(dbapi_conn, _rec):  # noqa: ANN001
            try:
                dbapi_conn.call_timeout = int(query_timeout_s * 1000)
            except Exception:
                pass
    elif p.dialect == "sqlite":
        @event.listens_for(eng, "connect")
        def _sqlite_ro(dbapi_conn, _rec):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA query_only=ON")
            cur.close()
    return eng


_HINTS = (
    ("ORA-01017", "The user name or password is wrong."),
    ("ORA-12541", "Nothing is listening at that host and port (is the Oracle listener running?)."),
    ("ORA-12514", "The listener does not know that service name."),
    ("ORA-12505", "The listener does not know that SID."),
    ("DPY-6005", "Could not reach the database server (check host, port and firewall)."),
    ("DPY-6001", "The listener does not know that service name."),
    ("password authentication failed", "The user name or password is wrong."),
    ("could not connect to server", "Could not reach the database server (check host and port)."),
)


def humanize(exc: Exception, p: ConnParams) -> str:
    raw = mask(str(getattr(exc, "orig", exc)) or exc.__class__.__name__, p.password).strip().splitlines()
    first = raw[0][:300] if raw else exc.__class__.__name__
    for key, hint in _HINTS:
        if key.lower() in " ".join(raw).lower():
            return f"{hint} ({first})"
    return first


def probe(engine: Engine, p: ConnParams) -> dict[str, Any]:
    """Open one connection, run a trivial query, return server info."""
    import time
    t0 = time.perf_counter()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1 FROM DUAL" if p.dialect == "oracle" else "SELECT 1"))
        version = None
        try:
            if p.dialect == "oracle":
                version = getattr(conn.connection.dbapi_connection, "version", None)
                try:
                    version = conn.execute(text("SELECT banner FROM v$version WHERE ROWNUM = 1")).scalar() or version
                except Exception:
                    conn.rollback()
            elif p.dialect == "postgresql":
                version = conn.execute(text("SELECT version()")).scalar()
            else:
                version = "SQLite " + str(conn.execute(text("SELECT sqlite_version()")).scalar())
        except Exception:
            version = version or None
    return {"server_version": str(version) if version else None, "ms": int((time.perf_counter() - t0) * 1000)}
