# DOCINT — Deployment Guide (Phase 26)

Oct 2026. Covers the two machines this system is meant to run across: your
RTX 5060 dev box (today) and the eventual L40S/company GPU server. The
architecture is already GPU-agnostic by config (no `cuda:0`, no hardcoded
model paths — see `app/core/config.py`), so moving to the GPU server is a
configuration change, not a code change.

## 1. Two-machine split

```
Dev / lightweight machine          GPU server (production)
──────────────────────────         ───────────────────────
FastAPI API (uvicorn)       <───▶  Redis (job queue)
React/Vite frontend                RQ worker process(es)
Oracle DB                          Pipeline models: Qwen (Ollama), Surya,
                                    Tesseract/img2table
```

Today both halves run on one box. The split becomes real the moment a
second machine runs `python -m app.workers.run_worker` (or whatever the
worker entrypoint is named) pointed at the same `REDIS_URL` — no other
change needed, since the API only ever enqueues jobs onto Redis and never
calls a pipeline directly.

## 2. Prerequisites

**Both machines:**
- Python 3.11 (matches `backend/venv`'s build)
- Oracle DB reachable (Oracle Free / XE is fine for dev; see
  `oracle_schema.sql` / `oracle_migration.sql` for the schema, and
  `backend/migrations/` for Alembic history)
- Redis reachable from both the API process and the worker process

**GPU server additionally needs:**
- An NVIDIA driver + CUDA runtime compatible with the PyTorch build pinned
  in `backend/requirements.txt` — **verify this explicitly**, do not assume
  compatibility; the plan's non-negotiable rule is never to hardcode a GPU
  model, but mismatched CUDA/driver versions are a day-one failure mode
  worth checking first.
- Ollama installed and the vision-LLM model (`AI_MODEL`, default
  `llama3.2-vision`) pulled, if the Qwen/Direct-LLM pipeline is enabled
  there.
- Enough VRAM for whichever pipelines are enabled concurrently — Phase 24
  (benchmarking) has not run yet, so do not assume multiple models can stay
  loaded simultaneously without measuring it first on this specific GPU.

## 3. Environment configuration

Copy `backend/.env.example` to `backend/.env` and set, per machine:

| Variable | Dev box | GPU server |
|---|---|---|
| `ORACLE_HOST`/`PORT`/`SERVICE_NAME`/`USER`/`PASSWORD` | local Oracle | shared Oracle, or its own |
| `REDIS_URL` | `redis://localhost:6379/0` | point at the **same** Redis both machines share |
| `SURYA_DEVICE` | `cpu` (today) | `cuda` once verified |
| `GPU_ENABLED` | `False` | `True` |
| `DIRECT_LLM_CALL` | as today | as today — unchanged by the split |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | wherever Ollama runs on that box |
| `MAX_PAGES_TO_PROCESS`, `OCR_MAX_WORKERS`, `GPU_INFERENCE_CONCURRENCY` | current values | re-tune only after Phase 24 benchmarking — don't guess |

Storage directories (`STORAGE_DIR`, `RESULTS_DIR`, `PAGES_DIR`, `CROPS_DIR`,
`THUMBNAILS_DIR`, `TEMP_DIR`, `LOG_DIR`) default to local paths under
`./data/`. If the API and worker run on different machines, these must
point at the **same shared location** (a mounted network share, or keep
everything on one box until a real multi-machine deployment need exists —
per the plan's Phase 3 note, do not build S3/NFS support speculatively).

## 4. Startup

Local dev: `start.bat` (Windows) / `start.sh` (Linux/Mac) already cover
this — they bring up the API, frontend dev server, and (if configured) a
worker process. Use them as-is for single-machine dev.

For a genuinely separate GPU worker machine:
1. Clone/sync the repo there (or just `backend/` + a `.env` pointed at the
   shared Redis/Oracle).
2. `pip install -r requirements.txt` in a venv matching the machine's CUDA
   setup (PyTorch's CUDA build must match the driver — see §2).
3. Start the worker process against the shared `REDIS_URL`.
4. Confirm `GET /api/v1/workers/health` from the API machine reports
   `redis_reachable: true` and, once a job runs, that pipeline results
   land correctly — this endpoint (added this session) also reports
   `cuda_available` and detected GPU name/memory on whichever machine the
   **API process** itself runs on; it does not yet poll a remote worker's
   GPU directly (that remains full worker-fleet scope, deferred per
   Phase 11).

## 5. Health checks

- `GET /api/v1/health` — basic liveness.
- `GET /api/v1/workers/health` — Redis reachability, GPU visibility on the
  API process, and which pipelines are registered.
- `GET /api/v1/processing/{job_id}/events` — full event timeline for one
  job, useful for confirming a worker actually picked up and processed a
  job end-to-end after a fresh deployment.

## 6. Restart / recovery

- RQ jobs already have a bounded retry policy (`JOB_MAX_RETRIES`,
  `JOB_RETRY_INTERVAL_SECONDS`) distinguishing transient failures from
  deterministic ones (Phase 10) — a worker restart mid-job does not lose
  the job outright, it retries per that policy.
- Alembic (`backend/migrations/`) is the only sanctioned way to change the
  schema on a running deployment — never hand-edit `core.py` models
  without a matching migration.
- No rollback tooling exists yet beyond Alembic downgrade and redeploying
  a previous build — this is tracked as remaining Phase 27 work, not
  solved here.

## 7. What this guide does NOT cover (honest gaps)

- Containerization — there is no Dockerfile/compose file in this repo.
  Deployment today means running the existing `start.bat`/`start.sh` /
  manual venv + uvicorn + worker process directly on each machine.
- CI/CD — no pipeline config exists in this repo.
- Authentication — intentionally out of scope per current product
  decision; if that changes later, auth config belongs in this guide too.
