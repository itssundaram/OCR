#!/usr/bin/env bash
# DOCINT launcher for Linux / macOS. Fully offline once the offline_package, node_modules and model folders are in place.
#   ./start.sh                 start API + every worker + frontend
#   DOCINT_DEV=1 ./start.sh    API with --reload (auto-restart on code change)
#   DOCINT_SKIP_PREFLIGHT=1    start even if the pre-start check reports a FAIL
#   DOCINT_API_WORKERS=2       force the number of API processes (default: chosen from the CPU)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo
echo "============================================================"
echo "  DOCINT - Offline AI Document Intelligence Platform"
echo "============================================================"
echo

if [ ! -f backend/.env ]; then
    echo "[ERROR] backend/.env not found. Copy backend/.env.example to backend/.env and fill in the values."
    exit 1
fi
[ -f frontend/.env ] || cp frontend/.env.example frontend/.env 2>/dev/null || true

# ---- 1. Python environment ---------------------------------------------------------------------------------------
echo "[1/4] Python environment"
cd backend
if [ ! -x venv/bin/python ]; then
    echo "      creating venv ..."
    python3 -m venv venv || { echo "[ERROR] could not create the venv (is python3-venv installed?)"; exit 1; }
fi
PY="$ROOT/backend/venv/bin/python"
if [ ! -f venv/.install_success ]; then
    if [ -d offline_package ]; then
        echo "      installing requirements from offline_package (no internet) ..."
        "$PY" -m pip install --no-index --find-links=offline_package -r requirements.txt || { echo "[ERROR] offline install failed"; exit 1; }
    else
        echo "      offline_package/ not found - installing from the internet (see README to build the offline folder) ..."
        "$PY" -m pip install -r requirements.txt || { echo "[ERROR] pip install failed"; exit 1; }
    fi
    echo OK > venv/.install_success
fi
echo "[OK]  venv ready"

# ---- 2. Redis (start a local one when it is installed but not running), then the pre-start check ------------------------
echo "[2/4] Checking Oracle, Redis, Tesseract, Ollama, model weights"
if command -v redis-cli >/dev/null 2>&1 && command -v redis-server >/dev/null 2>&1 && ! redis-cli ping >/dev/null 2>&1; then
    echo "      starting a local redis-server ..."
    redis-server --daemonize yes >/dev/null 2>&1; sleep 1
fi
if ! "$PY" scripts/preflight.py; then
    if [ "${DOCINT_SKIP_PREFLIGHT:-0}" != "1" ]; then
        echo "[FATAL] fix the FAIL lines above, or start anyway with DOCINT_SKIP_PREFLIGHT=1"
        exit 1
    fi
fi

# ---- 3. API + workers ---------------------------------------------------------------------------------------------------
echo "[3/4] Starting the API (port 8000) and the workers"
if [ "${DOCINT_DEV:-0}" = "1" ]; then RELOAD="--reload"; else RELOAD=""; fi
WORKERS_N="${DOCINT_API_WORKERS:-}"
if [ -z "$RELOAD" ] && [ -z "$WORKERS_N" ]; then
    WORKERS_N="$("$PY" -m app.system.explain --get api_workers --no-services 2>/dev/null | tail -1 | grep -E '^[0-9]+$' || echo 1)"
fi
[ -n "$WORKERS_N" ] && [ -z "$RELOAD" ] && WORKERS_ARG="--workers $WORKERS_N" || WORKERS_ARG=""
"$PY" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 $RELOAD $WORKERS_ARG &
API_PID=$!
# run_worker.py starts every worker (fast_ocr, learned_anchor, surya, tesseract, general) and restarts any that crash
"$PY" run_worker.py &
WORKER_PID=$!
cd "$ROOT"
sleep 4

# ---- 4. Frontend -----------------------------------------------------------------------------------------------------------
echo "[4/4] Starting the frontend (port 5173)"
if [ ! -d frontend/node_modules ]; then
    echo "[ERROR] frontend/node_modules is missing. Offline: copy it from a machine that ran 'npm ci' (README, step 6)."
    kill "$API_PID" "$WORKER_PID" 2>/dev/null
    exit 1
fi
(cd frontend && npm run dev -- --host) &
FRONTEND_PID=$!

echo
echo "============================================================"
echo "  API:       http://localhost:8000      (docs: /docs, health: /api/v1/health)"
echo "  Frontend:  http://localhost:5173"
echo "  Press Ctrl+C to stop everything."
echo "============================================================"

stop_all() { trap - EXIT INT TERM; kill "$API_PID" "$WORKER_PID" "$FRONTEND_PID" 2>/dev/null; wait 2>/dev/null; }
trap stop_all EXIT INT TERM
wait
