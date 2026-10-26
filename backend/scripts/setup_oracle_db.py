#!/usr/bin/env python3
"""
Check the Oracle connection and (optionally) create the DOCINT tables. Windows and Linux.

    cd backend
    python scripts/setup_oracle_db.py                       # connect, list tables, say what is missing (changes nothing)
    python scripts/setup_oracle_db.py --apply-schema        # run ../oracle_schema.sql   (DESTRUCTIVE, asks first)
    python scripts/setup_oracle_db.py --apply-schema --dry-run   # only print the statements it would run

WARNING: oracle_schema.sql first DROPS every DOCINT table and then re-creates them, so --apply-schema
erases all documents, templates and results in that Oracle user. It asks you to type the user name to
confirm (or pass --yes in a script). Run it once on a new database, never on one you want to keep.

Connection values come from backend/.env (ORACLE_HOST, ORACLE_PORT, ORACLE_SERVICE_NAME, ORACLE_USER,
ORACLE_PASSWORD), or the DATABASE_URL line when that is set.
Alternative without Python: sqlplus USER/PASSWORD@host:1521/FREEPDB1 @oracle_schema.sql
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
SCHEMA_FILE = REPO / "oracle_schema.sql"
sys.path.insert(0, str(BACKEND))


def split_statements(sql: str) -> list[str]:
    """Split a SQL*Plus-style script into single statements the driver can run one by one.

    * a PL/SQL block (BEGIN / DECLARE ...) ends at a line holding only "/" and keeps its final ";"
    * any other statement ends at a line ending in ";" (the ";" is removed, the driver does not want it)
    * "--" comment lines and blank lines outside a statement are skipped; "COMMIT;" is left to the caller
    """
    out: list[str] = []
    buf: list[str] = []
    plsql = False
    for line in sql.splitlines():
        s = line.strip()
        if not buf:
            if not s or s.startswith("--"):
                continue
            plsql = s.upper().startswith(("BEGIN", "DECLARE"))
        if plsql:
            if s == "/":
                out.append("\n".join(buf).strip())
                buf, plsql = [], False
            else:
                buf.append(line)
            continue
        buf.append(line)
        if s.endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt.upper() != "COMMIT":
                out.append(stmt)
            buf = []
    if buf and not plsql and "\n".join(buf).strip():
        out.append("\n".join(buf).strip().rstrip(";"))
    return out


def connect():
    from app.core.config import settings
    import oracledb

    url = None
    if settings.DATABASE_URL:
        from sqlalchemy.engine import make_url
        url = make_url(settings.DATABASE_URL)
        user, password = url.username, url.password
        host, port = url.host, url.port or 1521
        service = (url.query or {}).get("service_name") or settings.ORACLE_SERVICE_NAME
    else:
        user, password = settings.ORACLE_USER, settings.ORACLE_PASSWORD
        host, port, service = settings.ORACLE_HOST, settings.ORACLE_PORT, settings.ORACLE_SERVICE_NAME
    print(f"Connecting to Oracle at {host}:{port}/{service} as {user} ...")
    try:
        conn = oracledb.connect(user=user, password=password, host=host, port=port, service_name=service,
                                tcp_connect_timeout=5.0)
    except Exception as exc:
        print(f"\n[FAIL] Could not connect: {exc}\n")
        print("Check: 1) the database and its listener are running (lsnrctl status)")
        print("       2) ORACLE_HOST / ORACLE_PORT / ORACLE_SERVICE_NAME / ORACLE_USER / ORACLE_PASSWORD in backend/.env")
        sys.exit(1)
    print(f"[OK] Connected. Oracle version {conn.version}")
    return conn, user


def expected_tables() -> set[str]:
    from app.db.models import core  # noqa: F401
    from app.db.database import Base
    return {t.upper() for t in Base.metadata.tables}


def list_tables(conn) -> set[str]:
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
    names = {r[0].upper() for r in cur.fetchall()}
    cur.close()
    return names


def apply_schema(conn, statements: list[str]) -> int:
    cur = conn.cursor()
    failed = 0
    for i, stmt in enumerate(statements, 1):
        try:
            cur.execute(stmt)
        except Exception as exc:
            failed += 1
            print(f"[!] statement {i} failed: {str(exc).splitlines()[0]}\n    {stmt[:100]!r}")
    conn.commit()
    cur.close()
    return failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply-schema", action="store_true", help="run oracle_schema.sql (drops and re-creates every table)")
    ap.add_argument("--schema", default=str(SCHEMA_FILE), help="path to the schema file (default: ../oracle_schema.sql)")
    ap.add_argument("--dry-run", action="store_true", help="with --apply-schema: print the statements, run nothing")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    args = ap.parse_args()

    if args.apply_schema:
        path = Path(args.schema)
        if not path.is_file():
            print(f"[FAIL] schema file not found: {path}")
            return 1
        statements = split_statements(path.read_text(encoding="utf-8"))
        if args.dry_run:
            for i, s in enumerate(statements, 1):
                print(f"-- [{i}/{len(statements)}]\n{s}\n")
            print(f"{len(statements)} statements (nothing was run)")
            return 0

    conn, user = connect()
    have, want = list_tables(conn), expected_tables()

    if args.apply_schema:
        print(f"\nThis DROPS and re-creates {len(want)} tables in the Oracle user {user!r}: all their data is lost.")
        if not args.yes and input(f"Type the user name ({user}) to continue: ").strip().upper() != str(user).upper():
            print("Cancelled, nothing was changed.")
            return 1
        failed = apply_schema(conn, statements)
        have = list_tables(conn)
        print(f"\nSchema applied ({len(statements) - failed}/{len(statements)} statements ok).")

    missing = sorted(want - have)
    print(f"\nTables in {user}: {len(have & want)}/{len(want)} DOCINT tables present")
    for t in sorted(want):
        print(f"  {'ok     ' if t in have else 'MISSING'} {t}")
    conn.close()
    if missing:
        print("\nTables are missing. Create them with:  python scripts/setup_oracle_db.py --apply-schema")
        return 1
    print("\n[OK] Database is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
