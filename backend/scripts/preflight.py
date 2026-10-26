"""
Check that this machine can run DOCINT, before you start it. Windows and Linux, works offline.

    cd backend
    (activate the venv)
    python scripts/preflight.py            # check everything
    python scripts/preflight.py --strict   # warnings also give a non-zero exit code

Each line is OK, WARN (works, but something is reduced) or FAIL (the app cannot run / extract).
Exit code: 0 = no FAIL, 1 = at least one FAIL. start.bat and start.sh run this first.
Nothing is changed: it only reads.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # .env and ./data/... are relative to the backend folder

RESULTS: list[tuple[str, str, str]] = []


def say(level: str, what: str, detail: str = "") -> None:
    RESULTS.append((level, what, detail))
    print(f"  [{level:<4}] {what}" + (f"  -  {detail}" if detail else ""), flush=True)


def check_python() -> None:
    v = sys.version_info
    if v < (3, 10):
        say("FAIL", f"Python {v.major}.{v.minor}", "3.10 or newer is needed")
    else:
        say("OK", f"Python {v.major}.{v.minor}.{v.micro}")
    if sys.prefix == sys.base_prefix:
        say("WARN", "not inside a virtual environment", "activate backend/venv first (start.bat / start.sh do this)")


# import name -> pip name (only the ones that matter for starting the API and the workers)
REQUIRED = {
    "fastapi": "fastapi", "uvicorn": "uvicorn", "sqlalchemy": "sqlalchemy", "oracledb": "oracledb",
    "redis": "redis", "rq": "rq", "fitz": "pymupdf", "PIL": "Pillow", "pytesseract": "pytesseract",
    "cv2": "opencv-python-headless", "numpy": "numpy", "pydantic_settings": "pydantic-settings",
    "structlog": "structlog", "httpx": "httpx", "ollama": "ollama", "img2table": "img2table",
    "pandas": "pandas", "psutil": "psutil", "multipart": "python-multipart", "jsonschema": "jsonschema",
}
# only the legacy surya / tesseract / learned_anchor pipelines need these (fast_ocr does not)
OPTIONAL = {"torch": "torch", "transformers": "transformers", "surya": "surya-ocr", "timm": "timm"}


def check_packages() -> None:
    missing = []
    for mod, pip_name in REQUIRED.items():
        try:
            importlib.import_module(mod)
        except Exception as exc:  # ImportError, or a DLL that Windows blocked
            missing.append(f"{pip_name} ({type(exc).__name__})")
    if missing:
        say("FAIL", "Python packages", "missing or unloadable: " + ", ".join(missing) +
            "  ->  pip install --no-index --find-links=offline_package -r requirements.txt")
    else:
        say("OK", "Python packages (API + fast_ocr)")
    gone = []
    for mod, pip_name in OPTIONAL.items():
        try:
            importlib.import_module(mod)
        except Exception:
            gone.append(pip_name)
    if gone:
        say("WARN", "model packages", "not installed: " + ", ".join(gone) + ". No surya worker will start and the surya pipeline will fail if chosen; use fast_ocr / learned_anchor.")
    else:
        say("OK", "model packages (torch, transformers, surya)")


def load_settings():
    if not (BACKEND / ".env").is_file():
        say("FAIL", "backend/.env", "not found  ->  copy .env.example to .env and fill it in")
        return None
    try:
        from app.core.config import settings
    except Exception as exc:
        say("FAIL", "settings (.env)", f"{type(exc).__name__}: {exc}")
        return None
    say("OK", "backend/.env loaded")
    if not settings.ORACLE_PASSWORD and not settings.DATABASE_URL:
        say("WARN", "ORACLE_PASSWORD is empty", "set it in .env unless your database accepts an empty password")
    return settings


def tcp_open(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_oracle(settings) -> None:
    """Database login + tables. Written for Oracle, but works for any SQLAlchemy URL (DATABASE_URL)."""
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.engine import make_url

    url = make_url(settings.database_url)
    name = url.get_backend_name()
    label = "Oracle" if name == "oracle" else f"Database ({name})"
    if url.host:
        port = url.port or (settings.ORACLE_PORT if name == "oracle" else 0)
        if port and not tcp_open(url.host, int(port)):
            say("FAIL", f"{label} {url.host}:{port}", "not reachable. Is the database / listener running?")
            return
    try:
        eng = create_engine(settings.database_url, future=True)
        with eng.connect():
            have = {t.upper() for t in inspect(eng).get_table_names()}
        eng.dispose()
    except Exception as exc:
        say("FAIL", f"{label} login", f"{type(exc).__name__}: {str(exc)[:160]}  (check ORACLE_USER / ORACLE_PASSWORD / ORACLE_SERVICE_NAME)")
        return
    from app.db.models import core  # noqa: F401  (registers the tables)
    from app.db.database import Base
    want = {t.upper() for t in Base.metadata.tables}
    absent = sorted(want - have)
    where = f"{url.host}:{url.port}" if url.host else str(url.database)
    if absent:
        say("FAIL", f"{label} tables", f"{len(absent)} missing ({', '.join(absent[:4])}{'...' if len(absent) > 4 else ''}).  Create them: python scripts/setup_oracle_db.py --apply-schema")
    else:
        say("OK", f"{label} {where}  ({len(want)} tables present)")


def check_redis(settings) -> None:
    import redis

    try:
        r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=3)
        r.ping()
        say("OK", f"Redis {settings.REDIS_URL}")
    except Exception as exc:
        say("FAIL", f"Redis {settings.REDIS_URL}", f"{type(exc).__name__}. Start it (Linux: redis-server, Windows: Memurai service)")


def check_tesseract(settings) -> None:
    cmd = settings.TESSERACT_CMD or shutil.which("tesseract")
    if not cmd and os.name == "nt":
        for cand in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
            if Path(cand).is_file():
                cmd = cand
                break
    if not cmd or not (Path(cmd).is_file() or shutil.which(cmd)):
        say("FAIL", "Tesseract", "not found. Install it and set TESSERACT_CMD in .env (see README)")
        return
    try:
        out = subprocess.run([cmd, "--list-langs"], capture_output=True, text=True, timeout=20).stdout.split()
    except Exception as exc:
        say("FAIL", "Tesseract", f"cannot run {cmd}: {type(exc).__name__}")
        return
    if "eng" not in out:
        say("FAIL", "Tesseract language data", "'eng' is missing from tessdata (copy eng.traineddata into the tessdata folder)")
    elif "osd" not in out:
        say("WARN", "Tesseract", "'osd' traineddata missing: sideways-page detection falls back to a slower method")
    else:
        say("OK", f"Tesseract {cmd}  (eng, osd)")


def check_ollama(settings) -> None:
    url = settings.OLLAMA_BASE_URL.rstrip("/")
    p = urlparse(url)
    if not tcp_open(p.hostname or "localhost", p.port or 11434):
        say("WARN", f"Ollama {url}", "not reachable. fast_ocr still works (digital PDFs + Tesseract); surya / tesseract / learned_anchor need it")
        return
    try:
        import httpx
        tags = httpx.get(f"{url}/api/tags", timeout=5).json()
        names = [m.get("name", "") for m in tags.get("models", [])]
    except Exception as exc:
        say("WARN", f"Ollama {url}", f"answered badly ({type(exc).__name__})")
        return
    model = settings.AI_MODEL
    if any(n == model or n.split(":")[0] == model for n in names):
        say("OK", f"Ollama model {model}")
    else:
        say("WARN", f"Ollama model {model}", f"not installed ({len(names)} others are). Run: ollama pull {model}   (offline: copy the .ollama/models folder)")


def check_weights(settings) -> None:
    root = Path(settings.hf_home_path)
    present = sorted(p.name[8:].replace("--", "/") for p in root.glob("models--*") if p.is_dir()) if root.is_dir() else []
    if not present:
        say("WARN", f"model weights in {root}", "none. Only fast_ocr will work. Fill with: python scripts/download_models.py (on a connected PC, then copy the folder)")
        return
    need = ["vikp/surya_det3", "vikp/surya_rec2", "vikp/surya_layout3"]
    lack = [n for n in need if n not in present]
    if lack:
        say("WARN", "Surya weights", "missing: " + ", ".join(lack) + "  (surya pipeline will fail)")
    else:
        say("OK", f"model weights ({len(present)} repos in {root})")
    if settings.HANDWRITING_FALLBACK_ENABLED:
        hw = "microsoft/trocr-large-handwritten"
        say("WARN" if hw not in present else "OK", "handwriting fallback is ON",
            ("weights missing: " if hw not in present else "needs about 2 GB RAM per worker; ") +
            "set HANDWRITING_FALLBACK_ENABLED=false on a small / CPU-only PC")


def check_memory() -> None:
    try:
        import psutil
        gb = psutil.virtual_memory().total / 1024 ** 3
        free = psutil.virtual_memory().available / 1024 ** 3
        say("OK" if gb >= 12 else "WARN", f"RAM {gb:.0f} GB total, {free:.0f} GB free",
            "" if gb >= 12 else "under 12 GB: set WORKER_PIPELINES=fast_ocr,tesseract and HANDWRITING_FALLBACK_ENABLED=false")
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="WARN also fails")
    ap.add_argument("--json", action="store_true", help="print the result list as JSON at the end")
    args = ap.parse_args()
    print("DOCINT preflight\n")
    check_python()
    check_packages()
    settings = load_settings()
    if settings is not None:
        check_oracle(settings)
        check_redis(settings)
        check_tesseract(settings)
        check_ollama(settings)
        check_weights(settings)
    check_memory()
    fails = [r for r in RESULTS if r[0] == "FAIL"]
    warns = [r for r in RESULTS if r[0] == "WARN"]
    print(f"\n{len(fails)} FAIL, {len(warns)} WARN" + ("  -> fix the FAIL lines before starting" if fails else "  -> ready to start"))
    if args.json:
        print(json.dumps(RESULTS))
    return 1 if fails or (args.strict and warns) else 0


if __name__ == "__main__":
    sys.exit(main())
