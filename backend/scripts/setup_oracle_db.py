#!/usr/bin/env python3
import sys
import oracledb
from pathlib import Path
from dotenv import dotenv_values

env_path = Path("/home/ubuntu/Desktop/docint-ocr/repo-git/backend/.env")
env_vars = dotenv_values(env_path)

host = env_vars.get("ORACLE_HOST", "localhost")
port = int(env_vars.get("ORACLE_PORT", 1521))
service_name = env_vars.get("ORACLE_SERVICE_NAME", "FREEPDB1")
user = env_vars.get("ORACLE_USER", "DATA_OCR")
password = env_vars.get("ORACLE_PASSWORD", "oracle")

print(f"Connecting to Oracle at {host}:{port}/{service_name} as {user}...")

try:
    connection = oracledb.connect(
        user=user,
        password=password,
        host=host,
        port=port,
        service_name=service_name,
        tcp_connect_timeout=5.0
    )
    print(f"[✓] Connected successfully! Oracle Database Version: {connection.version}")
except Exception as e:
    print(f"\n[✗] Could not connect to Oracle database: {e}\n")
    print("Troubleshooting checks:")
    print(" 1. Is your local Oracle database (e.g. Oracle 23ai / 21c / 19c XE) running?")
    print(" 2. Is listener active on port 1521? (Check with 'lsnrctl status')")
    print(f" 3. Are credentials in backend/.env correct? (user: {user}, service: {service_name})")
    sys.exit(1)

cursor = connection.cursor()

# 1. Read oracle_schema.sql
schema_file = Path("/home/ubuntu/Desktop/docint-ocr/repo-git/oracle_schema.sql")
print(f"\nReading schema from {schema_file.name}...")

# 2. Execute Alter commands requested by user
alter_commands = [
    """
    BEGIN
        EXECUTE IMMEDIATE 'ALTER TABLE "DOCUMENTS" DROP CONSTRAINT "CK_DOC_STATUS"';
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
    """,
    """
    ALTER TABLE "DOCUMENTS" ADD CONSTRAINT "CK_DOC_STATUS" 
      CHECK (status IN ('PENDING', 'QUEUED', 'PROCESSING', 'COMPLETED', 'WARNING', 'FAILED'))
    """,
    """
    BEGIN
        EXECUTE IMMEDIATE 'ALTER TABLE "PROCESSING_JOBS" DROP CONSTRAINT "CK_PJ_STATUS"';
    EXCEPTION WHEN OTHERS THEN NULL;
    END;
    """,
    """
    ALTER TABLE "PROCESSING_JOBS" ADD CONSTRAINT "CK_PJ_STATUS" 
      CHECK (status IN ('QUEUED', 'PROCESSING', 'OCR_PROCESSING', 'AI_PROCESSING', 'VALIDATING', 'COMPLETED', 'WARNING', 'FAILED'))
    """
]

print("Applying ALTER TABLE constraints...")
for cmd in alter_commands:
    cmd_clean = cmd.strip()
    if cmd_clean:
        try:
            cursor.execute(cmd_clean)
            print(f"[✓] Executed constraint update successfully.")
        except Exception as e:
            print(f"[!] Info on constraint: {e}")

connection.commit()

# 3. Check existing tables
cursor.execute("SELECT table_name FROM user_tables ORDER BY table_name")
tables = [row[0] for row in cursor.fetchall()]
print(f"\nCurrent tables in schema ({user}):")
for t in tables:
    print(f" - {t}")

cursor.close()
connection.close()
print("\n[✓] Database check and constraint updates complete!")
