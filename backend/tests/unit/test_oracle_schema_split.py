"""scripts/setup_oracle_db.py: the SQL*Plus-style schema is split into statements the Oracle driver accepts."""
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND / "scripts"))
from setup_oracle_db import split_statements  # noqa: E402

SAMPLE = """
-- comment
BEGIN
   EXECUTE IMMEDIATE 'DROP TABLE A CASCADE CONSTRAINTS';
EXCEPTION WHEN OTHERS THEN NULL;
END;
/
CREATE TABLE A (
    id NUMBER NOT NULL,
    CONSTRAINT pk_a PRIMARY KEY (id)
);

-- seed
INSERT INTO A (id) VALUES (1);
COMMIT;
"""


def test_plsql_block_keeps_its_semicolons_and_plain_statements_lose_theirs():
    s = split_statements(SAMPLE)
    assert len(s) == 3
    assert s[0].startswith("BEGIN") and s[0].rstrip().endswith("END;")
    assert "EXCEPTION WHEN OTHERS THEN NULL;" in s[0]
    assert s[1].startswith("CREATE TABLE A") and not s[1].endswith(";")
    assert s[2] == "INSERT INTO A (id) VALUES (1)"          # COMMIT is left to the caller


def test_no_statement_is_empty_or_a_slash():
    assert all(x and x != "/" for x in split_statements(SAMPLE))


SCHEMA = BACKEND.parent / "oracle_schema.sql"


@pytest.mark.skipif(not SCHEMA.is_file(), reason="oracle_schema.sql lives at the repository root")
def test_real_schema_creates_exactly_the_tables_the_models_declare():
    from app.db.models import core  # noqa: F401
    from app.db.database import Base

    stmts = split_statements(SCHEMA.read_text(encoding="utf-8"))
    created = {m.group(1).upper() for s in stmts if (m := re.match(r"CREATE TABLE\s+(\w+)", s, re.I))}
    assert created == {t.upper() for t in Base.metadata.tables}
    assert not any(s.rstrip().endswith(";") for s in stmts if not s.upper().startswith("BEGIN"))
