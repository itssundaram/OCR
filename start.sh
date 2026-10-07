#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "============================================================"
echo "  DOCINT — Offline AI Document Intelligence Platform"
echo "============================================================"
echo ""

[ ! -f "backend/.env" ] && echo "[ERROR] backend/.env not found." && exit 1
[ ! -f "frontend/.env" ] && cp frontend/.env.example frontend/.env 2>/dev/null || true

echo "[1/6] Checking Oracle Database connection..."
cd backend && python scripts/check_oracle.py; [ $? -ne 0 ] && echo "[FATAL] Oracle unreachable." && exit 1
cd ..

echo "[2/6] Applying database migrations..."
cd backend && python -m alembic upgrade head; cd ..
echo "[OK]   Migrations applied."

echo "[3/6] Starting Redis (Docker)..."
docker compose up -d redis; sleep 3
cd backend && python scripts/check_redis.py; [ $? -ne 0 ] && echo "[ERROR] Redis not responding." && exit 1
cd ..

echo "[4/6] Checking AI model availability..."
cd backend && python scripts/check_models.py || true; cd ..

echo "[5/6] Starting FastAPI backend + Worker..."
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload &
API_PID=$!
python -m app.workers.main &
WORKER_PID=$!
cd ..; sleep 4

echo "[6/6] Starting Frontend (Port 5173)..."
cd frontend && npm run dev -- --host &
FRONTEND_PID=$!; cd ..

echo ""
echo "============================================================"
echo "  API:      http://localhost:8001"
echo "  Health:   http://localhost:8001/api/v1/health"
echo "  Frontend: http://localhost:5173"
echo "============================================================"
echo "Press Ctrl+C to stop all services."

trap "kill $API_PID $WORKER_PID $FRONTEND_PID 2>/dev/null; docker compose stop redis" EXIT
wait
