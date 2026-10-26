# DOCINT: every command and every setting

This is document 3 of 3. Commands are shown for **Windows** (PowerShell / cmd) and **Linux** (bash) where they differ. Paths are relative to the project folder unless stated. Settings live in `backend/.env` (copy of `backend/.env.example`, which has the same list with comments) and `frontend/.env`.

---

## Part A. Commands

### A1. Start and stop

| What | Windows | Linux |
|---|---|---|
| Start everything (API + all workers + frontend) | `start.bat` | `./start.sh` |
| Start with auto-reload while editing code | edit `start.bat` (already uses `--reload`) | `DOCINT_DEV=1 ./start.sh` |
| Start even if the pre-start check has a FAIL | `set DOCINT_SKIP_PREFLIGHT=1` then `start.bat` | `DOCINT_SKIP_PREFLIGHT=1 ./start.sh` |
| Stop | close the three windows, or Ctrl+C in each | Ctrl+C in the `start.sh` terminal |

By hand, one terminal each, from the project folder:

| Process | Windows | Linux |
|---|---|---|
| API | `cd backend` then `venv\Scripts\activate` then `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` | `cd backend && source venv/bin/activate && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| All workers | `python run_worker.py` (same venv) | `python run_worker.py` (same venv) |
| Frontend (dev) | `cd frontend` then `npm run dev -- --host` | `cd frontend && npm run dev -- --host` |
| Frontend (built) | `npm run build` then `npm run preview -- --host` | same |

- `--workers N` after `uvicorn ...` runs N API processes (Linux; `start.sh` picks N from the CPU, `DOCINT_API_WORKERS=N` forces it). Add `--reload` only while developing.
- **The API port is set only here** (`--port 8000`) and in two other places that must agree: `frontend/vite.config.js` (proxy target) and `VITE_API_BASE_URL` / `VITE_API_HOST` in `frontend/.env`. There is no `API_PORT` setting.
- `python run_worker.py` starts one worker per pipeline (`fast_ocr`, `learned_anchor`, `surya` if installed, `tesseract`) plus a general worker, restarts any that crash, and stops them all on Ctrl+C / kill.
  - `python run_worker.py --list` shows what it would start and exits.
  - `python run_worker.py --only fast_ocr,tesseract` starts just those (the rest run on the general worker). Same as `WORKER_PIPELINES=fast_ocr,tesseract` in `.env`.
- `python run_fast_worker.py` runs only the fast_ocr worker (not needed when `run_worker.py` runs).

### A2. First-time setup

| What | Windows | Linux |
|---|---|---|
| Create the venv | `cd backend` then `python -m venv venv` | `cd backend && python3 -m venv venv` |
| Activate it | `venv\Scripts\activate` | `source venv/bin/activate` |
| Install packages offline | `python -m pip install --no-index --find-links=offline_package -r requirements.txt` | same |
| Install packages online (only to build `offline_package`) | `python -m pip install -r requirements.txt` | same |
| Build `offline_package` (internet PC, same OS and Python) | `python -m pip download -r requirements.txt -d offline_package` | same |
| Frontend packages (internet PC) | `cd frontend` then `npm ci` | `cd frontend && npm ci` |
| Settings files | `copy backend\.env.example backend\.env` and `copy frontend\.env.example frontend\.env` | `cp backend/.env.example backend/.env` and `cp frontend/.env.example frontend/.env` |

### A3. Checks and diagnostics (from `backend`, venv active)

| Command | What it tells you |
|---|---|
| `python scripts/preflight.py` | OK / WARN / FAIL for Python, packages, `.env`, Oracle login and tables, Redis, Tesseract (+ `eng`, `osd`), Ollama and model, model weights, RAM. Exit code 1 on any FAIL. `--strict` makes WARN fail too; `--json` adds machine-readable output. Windows: `python scripts\preflight.py` |
| `python -m app.system.explain` | what hardware was detected (GPU, VRAM, CPU, RAM) and which worker/thread counts were chosen and why. Same as `GET /api/v1/system/profile` |
| `python run_worker.py --list` | which workers will start |
| `curl http://localhost:8000/api/v1/health` | API, Oracle, Redis, workers, Ollama, storage |
| `python scripts/download_models.py --check` | which model weights are present in `data/models/huggingface`, plus Tesseract and Ollama status |
| `python scripts/setup_oracle_db.py` | connect to Oracle and list which DOCINT tables exist (changes nothing) |

### A4. Database

| Command | Effect |
|---|---|
| `python scripts/setup_oracle_db.py --apply-schema --dry-run` | print the 50 statements of `oracle_schema.sql`, run nothing |
| `python scripts/setup_oracle_db.py --apply-schema` | **drops and re-creates every DOCINT table**, seeds the 6 departments; asks you to type the Oracle user name first (`--yes` skips the question) |
| `sqlplus DATA_OCR/password@localhost:1521/FREEPDB1 @oracle_schema.sql` | the same without Python |

The schema comes only from `oracle_schema.sql`. The `migrations/` folder and `alembic` are not used for this deployment: do not run `alembic upgrade` on a database made from the SQL file.

User creation (administrator, in the pluggable database):

```sql
CREATE USER DATA_OCR IDENTIFIED BY "choose_a_password" DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS;
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW TO DATA_OCR;
```

### A5. Models and external programs

| Command | Purpose |
|---|---|
| `python scripts/download_models.py` | download missing Surya / TATR / TrOCR weights into `MODEL_CACHE_DIR/huggingface` (needs internet; copy `backend/data/models` to the offline machine afterwards) |
| `python scripts/download_models.py --dest <folder>` | download somewhere else |
| `ollama pull qwen2.5vl:3b` | fetch the vision model (internet PC) |
| `ollama list` / `ollama ps` | models installed / models loaded in memory |
| `ollama serve` | start Ollama (Linux without a service; on Windows it runs as an app) |
| `tesseract --list-langs` | Windows: `"C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs`; must list `eng` and `osd` |
| `redis-cli ping` | must answer `PONG` (Memurai: `memurai-cli ping`) |
| Linux Redis service | `sudo systemctl enable --now redis-server` |

### A6. Testing and measuring (from `backend`, venv active)

| Command | Purpose |
|---|---|
| `python -m pytest tests/unit -q` | the unit suite (no Oracle, Redis or Ollama needed; SQLite in memory). Linux: prefix `TESSERACT_CMD=/usr/bin/tesseract` if Tesseract is not on PATH; Windows: `set TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` first |
| `python scripts/api_smoke_test.py --docs C:\sundaram\OCR_DOCS` | end-to-end through the live API with real sample PDFs (API + `run_worker.py` must be running). Options: `--base http://host:8000/api/v1`, `--pipeline fast_ocr`, `--only TMS`, `--endpoints`, `--timeout 420`. It only adds templates named `smoke_*` |
| `python scripts/bench_fast.py --docs <folder-or-pdf> [--pages 3] [--cold] [--limit N] [--json out.json]` | time the fast_ocr read path stage by stage (no Redis, Oracle or Ollama) |
| `python scripts/accuracy_eval.py tests/golden/*.json --docs-root <folder>` | field accuracy against hand-checked values; options `--vlm`, `--by-family`, `--json out.json` |
| `python scripts/ocr_text_eval.py --docs-root <folder> --family PSG --rotate 270` | OCR word recall against the PDF's own text layer |
| `cd frontend` then `npm run build` | production build of the UI (also a quick syntax check) |

### A7. Everyday maintenance

| Task | How |
|---|---|
| Logs | `backend/data/logs/`, the Logs page in the UI, and the console windows |
| Free disk space | `backend/data/temp`, `backend/data/pages`, `backend/data/crops`, `backend/data/ocr_cache` grow over time; nothing deletes them automatically (the OCR cache trims itself to `FAST_OCR_CACHE_MAX_MB` / `_MAX_AGE_DAYS`) |
| Clear the install marker (force a re-install on next start) | delete `backend/venv/.install_success` |
| See and change settings live | most settings need an API and worker restart after editing `.env` |

---

## Part B. Settings you must set

All in `backend/.env`. A missing line uses the default in `app/core/config.py`.

| Variable | Example | Why |
|---|---|---|
| `ORACLE_HOST` | `localhost` | Oracle server name or IP |
| `ORACLE_PORT` | `1521` | listener port |
| `ORACLE_SERVICE_NAME` | `FREEPDB1` | the pluggable database service |
| `ORACLE_USER` | `DATA_OCR` | schema owner that holds the DOCINT tables |
| `ORACLE_PASSWORD` | `...` | no default. Special characters (`@ : / #`) are fine |
| `DATABASE_URL` | `oracle+oracledb://DATA_OCR:pw@host:1521/?service_name=FREEPDB1` | optional: one URL instead of the five lines above; wins when set |
| `REDIS_URL` | `redis://localhost:6379/0` | job queue |
| `TESSERACT_CMD` | `C:\Program Files\Tesseract-OCR\tesseract.exe` / `/usr/bin/tesseract` | path to Tesseract; unset is fine on Linux when it is on PATH |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama address (the code default is port 11435, so set it) |
| `AI_MODEL` | `qwen2.5vl:3b` | the vision model name exactly as `ollama list` shows it |

## Part C. Settings you should review

| Variable | Recommended | Why |
|---|---|---|
| `OFFLINE_MODE` | `true` | models read only from `MODEL_CACHE_DIR`; connections to public addresses are refused (private networks, Oracle, Redis, Ollama still work) |
| `HANDWRITING_FALLBACK_ENABLED` | `false` on a CPU-only or small-RAM PC | the handwriting model needs about 2 GB per worker; with it off, pages keep the OCR text and are flagged for review |
| `WORKER_PIPELINES` | `auto`; `fast_ocr,tesseract` on small machines | which workers `run_worker.py` starts |
| `OLLAMA_REQUEST_TIMEOUT_S` | `180`; `600` on CPU if you use model-based pipelines | an Ollama call longer than this fails the job with a clear message |
| `DIRECT_LLM_CALL` | `true` | legacy flow: page images go straight to the vision model (needs `LLM_PAYLOAD_MODE=image` or `both`); `false` = Tesseract text first |
| `LLM_PAYLOAD_MODE` | `image` | `text`, `image` or `both` |
| `OCR_STRATEGY` | `surya_with_fallback` | legacy flow: `tesseract_only`, `surya`, `surya_with_fallback` |
| `DB_ADMIN_UI_ENABLED` | `false` | the Database page; there is no login, so keep it off unless needed |
| `MODEL_CACHE_DIR` | `./data/models` | where model weights live |
| `FAST_VLM_FALLBACK_ENABLED` | `true`; `false` for a model-free fast_ocr | let the vision model re-read single doubtful fields |
| `WORKER_GENERAL` | `true` | start the general worker (compare jobs and fallback for any pipeline) |
| `OFFLINE_ALLOW_HOSTS` | empty | extra hosts the offline guard may reach (comma separated); the DB, Redis and Ollama hosts are allowed automatically |
| `HF_HOME_OVERRIDE` | unset | only when HuggingFace's cache must sit outside `MODEL_CACHE_DIR/huggingface` |
| `OLLAMA_NUM_PREDICT` | `8192` | most tokens one model answer may have |
| `OLLAMA_NUM_CTX` | `16384` | model context window; lower it (4096-8192) if Ollama runs out of memory |
| `OLLAMA_KEEP_ALIVE` | `30m` | how long Ollama keeps the model loaded between jobs |
| `DB_ADMIN_ALLOWED_HOSTS` | empty | client addresses (IPs, CIDR ranges, comma separated) allowed to use the Database page besides this machine |
| `DB_ADMIN_MAX_ROWS` | `500` | most rows one preview returns |
| `DB_ADMIN_QUERY_TIMEOUT_S` | `15` | a preview query running longer is cancelled |
| `DB_ADMIN_CONNECT_TIMEOUT_S` | `10` | connection timeout for the Database page |
| `DB_ADMIN_SESSION_TTL_S` | `1800` | an idle Database-page connection closes after this |
| `DB_ADMIN_DIR` | `backend/data/db_admin` | where saved (encrypted) connections live |

## Part D. Every other backend setting

Defaults are the values in `app/core/config.py`. Uncomment the line in `.env` only to change it.

### Application

| Variable | Default | What it does |
|---|---|---|
| `APP_NAME` | `DocInt` | shown in the API docs and logs |
| `APP_ENV` | `development` | development / staging / production |
| `APP_VERSION` | `1.0.0` | reported by /api/v1/health |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |

### Storage folders (relative to the backend folder, or absolute)

| Variable | Default | What it does |
|---|---|---|
| `STORAGE_DIR` | `./data/documents` | uploaded files |
| `RESULTS_DIR` | `./data/results` | result files |
| `THUMBNAILS_DIR` | `./data/thumbnails` | page thumbnails |
| `TEMP_DIR` | `./data/temp` | scratch space; nothing cleans it automatically, empty it now and then |
| `PAGES_DIR` | `./data/pages` | rendered page images (evidence) |
| `CROPS_DIR` | `./data/crops` | field / table crops (evidence) |
| `LOG_DIR` | `./data/logs` | log files |
| `MAX_UPLOAD_SIZE_MB` | `50` | larger uploads are refused with FILE_TOO_LARGE (413) |
| `MAX_PAGES_TO_PROCESS` | `0` | 0 = every page |

### Redis / queues

| Variable | Default | What it does |
|---|---|---|
| `REDIS_QUEUE_NAME` | `docint` | general queue |
| `JOB_TIMEOUT_SECONDS` | `300` | ceiling for a legacy-pipeline job |
| `JOB_MAX_RETRIES` | `2` | retries only for transient failures (Redis/DB blips) |
| `JOB_RETRY_INTERVAL_SECONDS` | `30` | wait between those retries |

### Which OCR engine the legacy (non fast_ocr) flow uses

| Variable | Default | What it does |
|---|---|---|
| `OCR_STRATEGY` | `surya` | tesseract_only / surya / surya_with_fallback |
| `USE_PIPELINE_REGISTRY` | `false` | reserved; changes nothing today |
| `AI_PROVIDER` | `ollama` | ollama / transformers |
| `LLM_IMAGE_MAX_PX` | `800` | longest side of images sent to the model |
| `LLM_CLEANING_ENABLED` | `true` | legacy flow: send low-confidence text to the model to fix spelling |
| `LLM_CLEANING_THRESHOLD` | `0.85` | below this OCR confidence the text is cleaned |

### Hardware

| Variable | Default | What it does |
|---|---|---|
| `GPU_ENABLED` | `false` | true on a machine with an NVIDIA GPU that PyTorch can use |
| `DEVICE` | `cpu` | cpu / cuda / mps |
| `SURYA_DEVICE` | `cpu` | set cuda on a GPU server |
| `GPU_INFERENCE_CONCURRENCY` | `0` | 0 = unlimited threads inside GPU model calls |

### Surya / handwriting / preprocessing

| Variable | Default | What it does |
|---|---|---|
| `SURYA_LAYOUT_ENABLED` | `true` | surya pipeline: detect page layout regions first |
| `SURYA_LAYOUT_CONFIDENCE_THRESHOLD` | `0.3` | ignore layout regions below this score |
| `HANDWRITING_FALLBACK_THRESHOLD` | `0.80` | regions below this OCR confidence go to the handwriting model |
| `PREPROCESS_ORIENTATION_ENABLED` | `true` | legacy flow: auto-rotate pages |
| `PREPROCESS_DESKEW_ENABLED` | `true` | legacy flow: straighten slightly tilted pages |
| `PREPROCESS_CROP_BORDERS_ENABLED` | `false` | legacy flow: cut dark scanner borders |
| `CONFIDENCE_THRESHOLD` | `0.75` | field below this is flagged needs_review |
| `FIELD_LEVEL_FALLBACK_ENABLED` | `false` | reserved; only flags weak fields today |

### Tesseract / rendering

| Variable | Default | What it does |
|---|---|---|
| `TESSERACT_TIMEOUT` | `30` | seconds per page |
| `TESSERACT_OMP_THREADS` | `1` | 1 is fastest when pages are read in parallel |
| `SCANNED_PAGE_TEXT_THRESHOLD` | `50` | fewer extractable characters = scanned page |
| `OCR_DPI_NATIVE` | `2.0` | render scale for digital PDF pages |
| `OCR_DPI_SCANNED` | `3.0` | render scale for scanned pages (higher = more accurate, slower) |
| `OCR_DPI_DIRECT_LLM` | `1.0` | render scale when images go straight to the model |
| `OCR_MAX_WORKERS` | `4` | parallel pages in the legacy flow |
| `IMG2TABLE_SKIP_NATIVE` | `true` | digital pages: use PyMuPDF text instead of img2table |
| `IMG2TABLE_MAX_PASSES` | `2` | 1 bordered only, 2 + implicit, 3 + borderless |

### Key : value discovery and tables (learned_anchor + all pipelines)

| Variable | Default | What it does |
|---|---|---|
| `KV_DISCOVERY_ENABLED` | `true` | also return every "Label: value" pair found on the page |
| `KV_FIRST_ENABLED` | `true` | match template fields to those pairs first (fastest path) |
| `KV_FIRST_MIN_CONFIDENCE` | `0.6` | a matched pair must have at least this OCR confidence |
| `KV_FIRST_MIN_MATCH_RATIO` | `1.0` | share of template fields that must match (1.0 = all) |
| `TABLE_REGION_DETECTOR` | `lines` | lines / none / surya (surya is very slow on CPU) |
| `TABLE_GEOMETRY_FIRST` | `true` | read tables from ruling lines / word gaps before any model |
| `LABEL_SYNONYMS_FILE` | `(empty)` | JSON: list of lists of label phrases that mean the same field |
| `KNOWLEDGE_ENABLED` | `true` | TMS / MMS / PSG field vocabulary and voucher rules |

### fast_ocr pipeline: queue and process

| Variable | Default | What it does |
|---|---|---|
| `FAST_QUEUE_NAME` | `docint-fast` | queue the fast_ocr worker listens on |
| `FAST_JOB_TIMEOUT_S` | `600` | outer ceiling; stage limits do the real work |
| `FAST_WORKER_TIMEOUT` | `auto` | auto / signal / timer  (auto: Linux signal, Windows thread timer) |
| `FAST_AUTOSTART_WORKER` | `false` | true = the API starts a fast worker if none is alive |
| `FAST_AUTOSTART_WAIT_S` | `25` | an upload waits this long for the autostarted worker |
| `FAST_ALLOW_BACKGROUND_FALLBACK` | `false` | false = answer 503 when Redis is down |
| `FAST_EVENT_STREAM_MAXLEN` | `5000` | live-event stream (SSE) size and lifetime |
| `FAST_EVENT_STREAM_TTL_S` | `3600` | keep a job's live events this long |
| `FAST_SSE_HEARTBEAT_S` | `15` | keep-alive ping on the live stream |
| `WORKER_MODE` | `auto` | auto / simple / fork |
| `WORKER_COUNT` | `1` | reported on the System page; the worker set is chosen by WORKER_PIPELINES |
| `WORKER_RESTART_MAX_BACKOFF_S` | `60` | a crashed worker restarts after 2, 4, 8 ... s up to this |
| `WORKER_STALL_DUMP_S` | `90` | log every thread's stack for a job stuck this long (0 = off) |
| `WORKER_HEARTBEAT_S` | `30` | one "worker alive" log line this often |

### fast_ocr pipeline: reading

| Variable | Default | What it does |
|---|---|---|
| `FAST_PAGE_THREADS` | `4` | pages read in parallel |
| `FAST_NATIVE_MIN_WORDS` | `8` | fewer text-layer words = treat the page as scanned |
| `FAST_IMAGE_PAGE_COVER` | `0.5` | page mostly images = scan even if it has a text layer |
| `FAST_TRUST_EMBEDDED_OCR` | `false` | true = use big text layers that scanners embedded |
| `FAST_EMBEDDED_OCR_MIN_WORDS` | `60` | how big such a layer must be to be trusted |
| `FAST_ORIENTATION_ENABLED` | `true` | re-read sideways scans turned 90/270 |
| `FAST_TABLES_ALWAYS` | `true` | look for ruled tables on every job |
| `FAST_TEXT_TABLES_ENABLED` | `true` | tables with no lines, from aligned text columns |
| `FAST_TABLE_TIMEOUT_S` | `20` | per page; timeout = "no table", never a stuck job |
| `FAST_TATR_FALLBACK_ENABLED` | `false` | borderless tables via TATR (needs weights in MODEL_CACHE_DIR) |
| `FAST_BARE_PAIRS_ENABLED` | `true` | also find "Label  value" pairs printed without a colon |
| `FAST_COPY_VOTE_ENABLED` | `true` | keep the best reading across copies of the same voucher |
| `FAST_LABEL_REREAD_ENABLED` | `true` | re-read the spot beside a found label that came back empty |
| `FAST_GAP_FILL_ENABLED` | `false` | whole-page second read; slow (~7 s per scanned page) |
| `FAST_GAP_FILL_BUDGET_S` | `3.0` | per page, when gap fill is on |
| `PREFETCH_ENABLED` | `true` | start processing when a file is chosen, before Execute |
| `PREFETCH_TTL_SECONDS` | `900` | an unused prefetch is discarded after this |

### fast_ocr pipeline: OCR cache

| Variable | Default | What it does |
|---|---|---|
| `FAST_OCR_CACHE_ENABLED` | `true` | same file bytes + page + engine = reuse OCR words |
| `FAST_OCR_CACHE_DIR` | `./data/ocr_cache` | where cached OCR words are kept |
| `FAST_OCR_CACHE_MAX_MB` | `512` | cache size cap |
| `FAST_OCR_CACHE_MAX_AGE_DAYS` | `14` | cache entries older than this are removed |

### fast_ocr pipeline: vision-model repair of single fields

| Variable | Default | What it does |
|---|---|---|
| `FAST_VLM_FALLBACK_ENABLED` | `true` | set false for a fully model-free fast_ocr |
| `FAST_VLM_TIMEOUT_S` | `40` | one crop read |
| `FAST_VLM_PARALLEL` | `2` | crop reads in flight at once |
| `FAST_VLM_BUDGET_S` | `45` | total model time per job |
| `FAST_VLM_MAX_FIELDS` | `8` | most model calls per job |
| `FAST_VLM_KEEP_ALIVE` | `30m` | how long Ollama keeps the model loaded for these calls |

### fast_ocr pipeline: background table refinement

| Variable | Default | What it does |
|---|---|---|
| `FAST_REFINE_ENABLED` | `true` | re-read flagged table cells after the first result is shown |
| `FAST_REFINE_QUEUE_NAME` | `docint-refine` | queue for background refinement jobs |
| `FAST_REFINE_SLICE_S` | `20` | one refine job works this long, saves, re-queues itself |
| `FAST_REFINE_BATCH` | `8` | cells saved and announced together |
| `FAST_REFINE_VLM_MAX` | `150` | vision-model cell reads per document |

## Part E. Settings in the process environment (not in `.env`)

Set in the shell before starting (`set NAME=value` on Windows, `NAME=value command` on Linux). They override the hardware auto-configuration. A value you put in `.env` always wins over an auto-chosen one.

| Variable | Effect |
|---|---|
| `DOCINT_DEV=1` | `start.sh`: start the API with `--reload` |
| `DOCINT_SKIP_PREFLIGHT=1` | `start.sh` / `start.bat`: start although the pre-start check reports a FAIL |
| `DOCINT_API_WORKERS` | number of API processes (`start.sh`) |
| `DOCINT_DEVICE` | force `cpu` / `cuda` |
| `DOCINT_GPU_INDEX`, `DOCINT_VRAM_BUDGET_MB`, `DOCINT_DTYPE`, `DOCINT_MICRO_BATCH_PAGES`, `DOCINT_GPU_CONCURRENCY`, `DOCINT_ONNX_PROVIDER` | GPU choices for GPU servers |
| `DOCINT_OLLAMA_NUM_CTX` | context size override |
| `DOCINT_CPU_WORKERS`, `DOCINT_PAGE_THREADS`, `DOCINT_OCR_CACHE_MB` | CPU-side overrides |
| `HF_HOME`, `TRANSFORMERS_CACHE`, `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE` | set automatically from `MODEL_CACHE_DIR` and `OFFLINE_MODE`; do not put them in `.env` (a stray `HF_HOME=1` would be taken literally) |

## Part F. Frontend settings (`frontend/.env`, read when the dev server starts or the UI is built)

| Variable | Default | Meaning |
|---|---|---|
| `VITE_API_BASE_URL` | `http://localhost:8000/api/v1` | where the browser sends API calls. For use from other PCs set `/api/v1` so calls go through the Vite proxy to port 8000 |
| `VITE_API_HOST` | `http://localhost:8000` | address shown on the Generate URL page; empty = the page's own address |

Restart `npm run dev` after editing. The proxy target (port 8000) is in `frontend/vite.config.js`.

## Part G. Ports

| Port | Used by | Change it in |
|---|---|---|
| 8000 | API | the `--port` of uvicorn, `frontend/vite.config.js`, `frontend/.env` |
| 5173 | frontend | `frontend/vite.config.js` (`server.port`) |
| 1521 | Oracle | `ORACLE_PORT` |
| 6379 | Redis | `REDIS_URL` |
| 11434 | Ollama | `OLLAMA_BASE_URL` |















python -m venv venv
venv\Scripts\activate
python -m pip install --no-index --find-links=offline_package -r requirements.lock.txt
python -m pip check





python3 -m venv venv
source venv/bin/activate
python -m pip install --no-index --find-links=offline_package -r requirements.lock.txt
python -m pip check