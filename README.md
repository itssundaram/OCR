# DOCINT: what it is and how to run it (fully offline, Windows and Linux)

This is document 1 of 3. The other two are `DEPARTMENT_API.md` (how departments call the software over HTTP) and `COMMANDS_AND_ENV.md` (every command and every setting).

---

## 1. What DOCINT is

DOCINT reads PDFs and scanned images and returns the values you ask for as JSON. You describe a document type once, as a **template** (a list of field names such as `iv_no`, `unit_from`, `date`, plus optional tables). From then on any file of that type, uploaded in the web UI or sent by another system over the API, comes back as structured data with a confidence per field and a box showing where each value was read on the page.

Everything runs on your own machines. No document, model request or telemetry leaves the network (`OFFLINE_MODE=true` blocks connections to public addresses).

**How a document flows**

```
 browser (React, :5173) ──proxy /api──►  FastAPI (:8000)  ──►  Oracle  (departments, templates, jobs, results)
 department systems ───── HTTP ───────►     │
                                            └─► Redis queue ─► workers (run_worker.py starts them all)
                                                                  ├─ fast_ocr      PyMuPDF text layer + Tesseract + geometry tables
                                                                  ├─ learned_anchor saved boxes / "Label: value" pairs, model only for gaps
                                                                  ├─ tesseract      Tesseract + vision model
                                                                  ├─ surya          Surya layout/OCR + vision model
                                                                  └─ general        compare jobs and fallbacks
                                            Ollama (:11434) holds the vision model; Tesseract is a local program
```

**Concepts**

- **Department**: a group such as `TMS`, `MMS`, `PSG`, `CUE_TMS`. Six are created by `oracle_schema.sql`.
- **Template**: one document type inside a department (`tms/backload`). A template version must be **activated** before uploads for it are accepted.
- **Pipeline**: the method that reads a document. Pick one per upload.

| Pipeline | Needs | Speed on a CPU-only PC | Use it for |
|---|---|---|---|
| `fast_ocr` (default in the UI) | Tesseract. Ollama is optional (only to re-read single doubtful fields) | seconds per page; never waits more than `FAST_VLM_BUDGET_S` on the model | everything, first choice |
| `learned_anchor` | Tesseract; Ollama only when fields are still missing after pair matching | fast when boxes/pairs match | templates whose boxes were confirmed by a person |
| `tesseract` | Tesseract + Ollama vision model | slow: minutes per page on CPU | comparison runs |
| `surya` | torch, transformers, surya weights in `data/models`, Ollama | very slow on CPU, good on a GPU | GPU servers |

All four are listed in the UI. `run_worker.py` starts one worker per pipeline and skips `surya` when `surya-ocr` is not installed; on a PC without the model packages (torch, transformers, surya) choose `fast_ocr` or `learned_anchor`, because `surya` would fail when it runs.

**Web pages**: Dashboard, Manage (departments, templates, field editor), OCR (upload, review, rotate, correct), Orchestration (run several pipelines and compare), Results, Generate URL, Logs, Database (optional, `DB_ADMIN_UI_ENABLED=true`), Help.

---

## 2. What has to be installed

| Component | Windows | Linux | Needed by |
|---|---|---|---|
| Python 3.10+ (3.11 tested) | python.org installer, tick "Add to PATH" | `python3`, `python3-venv` | everything |
| Node.js 18+ (20 LTS recommended) | nodejs.org installer | NodeSource / tarball | frontend |
| Oracle Database 19c+/23ai (service e.g. `FREEPDB1`) | Oracle installer or an existing server | same | everything |
| Redis | Memurai (Redis for Windows) | `redis-server` | job queue |
| Tesseract 5 with `eng` and `osd` language data | UB-Mannheim installer | `tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd` | all OCR |
| Ollama + vision model `qwen2.5vl:3b` | OllamaSetup.exe | Ollama tarball | model-based steps (optional for `fast_ocr`) |
| Model weights (Surya, TrOCR, TATR) in `backend/data/models` | `download_models.py` | same | surya / tesseract / handwriting only |

`python-oracledb` runs in thin mode: no Oracle client has to be installed.

---

## 3. Prepare everything on a PC that has internet

Do this once, on a machine with **the same operating system, CPU type and Python minor version** as the offline target (wheels are specific to all three). Then carry the folder across on a USB drive or file share.

1. **Get the project folder** (this repository) and open a terminal in it.

2. **Python packages** into `backend/offline_package`:

   Windows (PowerShell or cmd):
   ```
   cd backend
   python -m venv venv
   venv\Scripts\activate
   python -m pip install --upgrade pip
   python -m pip download -r requirements.txt -d offline_package
   ```
   Linux:
   ```
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip download -r requirements.txt -d offline_package
   ```
   The folder is large (torch alone is hundreds of MB; on Linux the default torch wheel also brings the NVIDIA libraries). Prove it is complete **before** going offline: create a throw-away venv and run the install command from step 6 below with the network cable out.

3. **Frontend packages**: `cd frontend` then `npm ci`. Copy the whole `frontend/node_modules` folder with the project (Node needs it at run time too, `npm install` cannot work offline).

4. **Model weights** (used by surya, tesseract-pipeline and the handwriting fallback; `fast_ocr` needs none):
   ```
   cd backend
   (venv active)
   python scripts/download_models.py
   ```
   It fills `backend/data/models/huggingface` and verifies each repository file by file. `python scripts/download_models.py --check` reports what is present, offline.

5. **Ollama model**: install Ollama, run `ollama pull qwen2.5vl:3b`, then copy the Ollama models folder (Windows `%USERPROFILE%\.ollama\models`, Linux `~/.ollama/models`, or `/usr/share/ollama/.ollama/models` when it runs as a service).

6. **Installers to carry**: Python, Node.js, Tesseract (Windows: UB-Mannheim build, includes `eng` and `osd`; Linux: the three `.deb` files and their dependencies, e.g. `apt-get install --download-only`), Redis / Memurai, Ollama (Linux: the `ollama-linux-amd64.tgz` from the releases page), and the Oracle installer if the target has no database yet.

What to put on the USB drive: the project folder (now containing `backend/offline_package`, `frontend/node_modules`, `backend/data/models`), the Ollama models folder, and the installers.

---

## 4. Set up the offline machine: Windows

Open **PowerShell** in the project folder (say `C:\DOCINT`).

1. **Install** Python, Node.js, Tesseract, Memurai (or Redis), Ollama, and Oracle (if needed) from the carried installers. Check:
   ```
   python --version
   node --version
   "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
   redis-cli ping
   ```
   The language list must contain `eng` and `osd`; `redis-cli ping` answers `PONG` (Memurai ships `memurai-cli`).

2. **Ollama model**: copy the carried `models` folder into `%USERPROFILE%\.ollama\models`, restart Ollama, then `ollama list` must show `qwen2.5vl:3b`.

3. **Oracle user and tables.** In SQL Developer or `sqlplus` as an administrator in the pluggable database (`FREEPDB1`):
   ```sql
   CREATE USER DATA_OCR IDENTIFIED BY "choose_a_password" DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS;
   GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW TO DATA_OCR;
   ```
   Then create the tables after step 5 below with `python scripts\setup_oracle_db.py --apply-schema`, or run `oracle_schema.sql` yourself (SQL Developer "Run Script", or `sqlplus DATA_OCR/password@localhost:1521/FREEPDB1 @oracle_schema.sql`).
   **`oracle_schema.sql` drops every DOCINT table first**: run it once on a new database, never on one that holds data you want.

4. **Settings files**:
   ```
   copy backend\.env.example backend\.env
   copy frontend\.env.example frontend\.env
   notepad backend\.env
   ```
   Set at least `ORACLE_PASSWORD`, and check `ORACLE_HOST`, `ORACLE_SERVICE_NAME`, `REDIS_URL`, `TESSERACT_CMD`, `OLLAMA_BASE_URL`, `AI_MODEL`. Full list: `COMMANDS_AND_ENV.md`.

5. **Python environment and packages** (`start.bat` also does this the first time):
   ```
   cd backend
   python -m venv venv
   venv\Scripts\activate
   python -m pip install --no-index --find-links=offline_package -r requirements.txt
   ```

6. **Check the machine**, still in `backend` with the venv active:
   ```
   python scripts\preflight.py
   ```
   Fix every `FAIL`. A `WARN` means a pipeline or feature is reduced, not that the app is broken. If the Oracle tables are missing, run `python scripts\setup_oracle_db.py --apply-schema` (it asks you to type the user name first).

7. **Start**: from the project folder run `start.bat`. It opens three windows (API, workers, frontend). Or by hand in three terminals:
   ```
   cd backend & venv\Scripts\activate & python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   cd backend & venv\Scripts\activate & python run_worker.py
   cd frontend & npm run dev -- --host
   ```

8. **Open** `http://localhost:5173`. Health check: `http://localhost:8000/api/v1/health`.

---

## 5. Set up the offline machine: Linux

Open a terminal in the project folder (say `/opt/docint`).

1. **Install** Python 3 with venv, Node.js, Tesseract (`dpkg -i` the carried packages, or `apt install --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd` from a local mirror), Redis, Ollama, Oracle. Check:
   ```
   python3 --version && node --version
   tesseract --list-langs
   sudo systemctl enable --now redis-server && redis-cli ping
   ```
   Ollama from the tarball: `sudo tar -C /usr -xzf ollama-linux-amd64.tgz`, then run `ollama serve` (or create a systemd service).

2. **Ollama model**: copy the carried `models` folder to `~/.ollama/models` (for the user that runs `ollama serve`), restart it, then `ollama list` shows `qwen2.5vl:3b`.

3. **Oracle user and tables**, as an administrator in the pluggable database:
   ```sql
   CREATE USER DATA_OCR IDENTIFIED BY "choose_a_password" DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS;
   GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW TO DATA_OCR;
   ```
   Tables: step 6 below. `oracle_schema.sql` drops every DOCINT table first, so run it once on a new database only.

4. **Settings files**:
   ```
   cp backend/.env.example backend/.env
   cp frontend/.env.example frontend/.env
   nano backend/.env
   ```
   Set `ORACLE_PASSWORD` and check the rest. `TESSERACT_CMD` can stay unset when `tesseract` is on `PATH`.

5. **Python environment and packages** (`start.sh` also does this the first time):
   ```
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   python -m pip install --no-index --find-links=offline_package -r requirements.txt
   ```

6. **Check the machine and create the tables**:
   ```
   python scripts/preflight.py
   python scripts/setup_oracle_db.py --apply-schema
   python scripts/preflight.py
   ```
   `setup_oracle_db.py --apply-schema --dry-run` prints the statements without running them.

7. **Start**: from the project folder `./start.sh` (make it executable once with `chmod +x start.sh`). It starts a local `redis-server` if one is installed but not running, runs the check, then starts the API, all workers and the frontend. `DOCINT_DEV=1 ./start.sh` adds auto-reload. By hand, three terminals:
   ```
   cd backend && source venv/bin/activate && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   cd backend && source venv/bin/activate && python run_worker.py
   cd frontend && npm run dev -- --host
   ```

8. **Open** `http://<server>:5173`. Health: `http://<server>:8000/api/v1/health`. Stop everything with Ctrl+C in the `start.sh` terminal.

---

## 6. First use

1. In **Manage**, pick a department and create a template: a code (`backload`) and its fields.
2. **Activate** that template version. Uploads for an inactive template fail with `TEMPLATE_NOT_FOUND`.
3. In **OCR**, choose the department and template, leave pipeline `fast_ocr`, pick a PDF and press Execute. Fields appear live; low-confidence ones are marked for review. You can correct values, rotate the page, and add a field found on the page to the template.
4. Departments that integrate by software use the API instead: see `DEPARTMENT_API.md`.

**Other PCs on the network** open `http://<server>:5173`. For that to work, `frontend/.env` must send API calls through the same address: set `VITE_API_BASE_URL=/api/v1` (the Vite server proxies it to port 8000) and restart the frontend. Open ports 5173 (UI) and 8000 (API) in the server's firewall.

---

## 7. When something fails

| What you see | Cause and fix |
|---|---|
| `Ollama did not answer within 180s (ReadTimeout)` | The vision model on a CPU needs minutes per page. Use `fast_ocr`, or raise `OLLAMA_REQUEST_TIMEOUT_S`, or give the machine a GPU. Check `ollama list` shows the model and `ollama ps` shows it loaded. |
| `Cannot reach Ollama at ... (connection refused)` | Ollama is not running or `OLLAMA_BASE_URL` is wrong. Start it (`ollama serve` on Linux) and check `ollama list`. `fast_ocr` does not need it. |
| `Surya layout: model weights are not in ...` | The weights were not copied to this machine. On a PC with internet run `python scripts/download_models.py`, copy `backend/data/models` across. `fast_ocr` needs no weights. |
| `Failed to load handwriting model ... not enough memory` | The handwriting model needs about 2 GB per worker. Set `HANDWRITING_FALLBACK_ENABLED=false` (jobs then finish and the field is flagged), and/or `WORKER_PIPELINES=fast_ocr,tesseract` to run fewer workers. With the flag on and the model failing, the app now degrades instead of failing the job, but memory is still being wasted. |
| Upload accepted, job stays `QUEUED` | No worker is running: start `python run_worker.py`, and check Redis (`redis-cli ping`). |
| `TEMPLATE_NOT_FOUND` | The template is not activated, or the department/code is misspelled. |
| `python scripts/preflight.py` says a package is missing or cannot load | The offline install was incomplete. Re-run the `pip install --no-index` command and read its error. On managed Windows PCs, Application Control can block `.pyd` files; `pandas` is pinned to 2.3.x for that reason. |
| Frontend loads but every call fails | The API is not on port 8000, or `frontend/.env` points somewhere else (see section 6). |
| Oracle login fails | `ORACLE_USER` / `ORACLE_PASSWORD` / `ORACLE_SERVICE_NAME` in `backend/.env`; test with `python scripts/setup_oracle_db.py` (read-only without flags). |
| Logs | `backend/data/logs/`, and the Logs page in the UI. |

---

## 8. What this document does not promise

- It was written and checked against the code and tested in a Linux sandbox (unit tests, the API with real PDFs, the `fast_ocr` pipeline, the frontend build). Windows behaviour, a real Oracle database, and Ollama-based pipelines on your hardware were **not** exercised there; `scripts/preflight.py` exists so the first run on the real machine tells you exactly what is missing.
- Building `offline_package` and `node_modules` needs one internet-connected machine of the same OS; nothing here can create them offline.
- There is no login: anyone who can reach port 8000 or 5173 can use the software. Keep it on a private network.
