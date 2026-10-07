# DocInt OCR - Offline Deployment Guide

To deploy the DocInt OCR application on an completely offline PC, you need to transfer not just the codebase, but also the databases, system dependencies, Python/Node environments, and crucially, all the AI models (which are usually downloaded on-the-fly).

Here is a comprehensive checklist and instructions on what to pack and how to configure it.

## 1. Things to Pack (The "Go Bag")

### A. The Codebase & Configs
- The entire `repo-git` directory.
- Your environment files (`backend/.env` and `frontend/.env`). Ensure you update the Database/Redis URLs to match the offline PC's configuration if they change.

### B. System Dependencies (Installers)
Download the offline installers or `.deb`/`.rpm` packages for the target offline OS:
- **Python 3.11+**
- **Node.js (v18+)**
- **Oracle Database** (If you don't use a separate DB server)
- **Redis Server** (Required for the RQ background worker)
- **Tesseract OCR**: You need the base package and the English language pack (`tesseract-ocr`, `tesseract-ocr-eng`).
- **Ollama**: The offline installer for Ollama.

### C. Python & Node Dependencies
You cannot run `pip install` or `npm install` without internet.
- **Python**: On a PC with internet (matching the target OS/Architecture), run:
  ```bash
  mkdir wheels
  pip download -r backend/requirements.txt -d ./wheels
  ```
  Take the `wheels` folder with you.
- **Node.js**: The easiest approach is to run `npm install` inside the `frontend` folder on an internet-connected PC with the same OS, and simply copy the entire `node_modules` folder over.

### D. AI Models (Crucial!)
The application relies on several large deep learning models downloaded via HuggingFace and Ollama. You must copy these caches over.

**1. HuggingFace Models (Surya, TATR, TrOCR)**
By default, HuggingFace downloads models to `~/.cache/huggingface/hub/`. Pack this entire folder. It contains:
- `vikp/surya_det3` (Surya Layout Detection)
- `vikp/surya_rec2` (Surya OCR)
- `microsoft/table-transformer-detection` (TATR)
- `microsoft/table-transformer-structure-recognition` (TATR)
- `microsoft/trocr-base-handwritten` (Handwriting Fallback)

**2. Ollama Models (Qwen)**
If you are using the local LLM extraction, Ollama stores its models in `~/.ollama/models/` (Linux) or `%USERPROFILE%\.ollama\models\` (Windows). You must copy this folder so `qwen2.5vl:3b` is available offline.

**3. PaddleOCR & PP-Structure Models**
If you are using the PaddleOCR or PP-Structure pipelines, Paddle automatically downloads its inference models to `~/.paddleocr/` (Linux) or `%USERPROFILE%\.paddleocr\` (Windows). You must pack this entire folder. It typically contains:
- `whl/det/en/en_PP-OCRv3_det_infer`
- `whl/rec/en/en_PP-OCRv4_rec_infer`
- `whl/layout/picodet_lcnet_x1_0_fgd_layout_infer`
- `whl/table/en_ppstructure_mobile_v2.0_SLANet_infer`

**4. Local Custom Binaries (`backend/bin/`)**
If you compiled or downloaded custom local binaries (e.g., `llama.cpp` builds or archive files located in `backend/bin/`), remember to copy this folder manually! This directory is intentionally ignored in `.gitignore` because of file size limits on GitHub, so it won't be in your git clone.

**5. Existing App Data & Database (Optional)**
If you wish to migrate your existing extraction templates, parsed documents, and execution logs to the offline PC, you need to manually copy the `backend/data/` directory. This contains your `docint.db` SQLite database (if not using Postgres) and the raw `pages/` and `thumbnails/` image directories. This is also ignored in git to prevent accidentally leaking sensitive uploaded documents.

---

## 2. Instructions for the Offline PC

### Step 1: Install System Services
1. Install Python, Node.js, Oracle Database, Redis, Tesseract OCR, and Ollama using the offline installers/packages you brought.
2. Start the Oracle Database and Redis services.
3. Create the necessary schema user in Oracle (matching your `.env` credentials).

### Step 2: Transfer AI Models
1. Copy the HuggingFace cache folder into the corresponding user directory on the offline PC: `~/.cache/huggingface/hub/`.
   *(Note: HuggingFace libraries automatically check this folder before trying to download from the internet. As long as the files are there, it will work completely offline).*
2. Copy the Ollama models folder to `~/.ollama/models/` and start the Ollama service. You can verify it works by running `ollama list`.

### Step 3: Setup the Backend
1. Move the `repo-git` directory to the offline PC.
2. Create a virtual environment: `python -m venv backend/venv`
3. Activate it: `source backend/venv/bin/activate` (Linux/Mac) or `backend\venv\Scripts\activate` (Windows)
4. Install the Python packages from your wheels folder:
   ```bash
   pip install --no-index --find-links=./wheels -r backend/requirements.txt
   ```
5. Provision the Database Tables:
   **Do not use Alembic.** Instead, locate the `oracle_schema.sql` file in the root directory and execute it directly against your Oracle Database using a tool like SQL Developer or SQL*Plus. This will create all tables, sequences, and insert seed data seamlessly.

### Step 4: Setup the Frontend
1. Place the populated `node_modules` folder inside the `frontend` directory.
2. Ensure your `frontend/.env` points to the correct backend IP (usually `http://localhost:8001`).

### Step 5: Start the Application
You will need three terminal windows to run the stack:

**Terminal 1 (Backend API):**
```bash
cd backend
source venv/bin/activate
uvicorn app.main:app --port 8001 --host 0.0.0.0
```

**Terminal 2 (Background Worker):**
```bash
cd backend
source venv/bin/activate
python run_worker.py
```

**Terminal 3 (Frontend):**
```bash
cd frontend
# You can use the production build for better performance:
npm run build
npm run preview
# Or for development mode:
npm run dev
```

### Environment Variable Checklist
Double-check your `backend/.env` on the offline PC:
- `DATABASE_URL`: Ensure it points to the local database instance.
- `REDIS_URL`: Ensure it points to the local Redis instance.
- `GPU_ENABLED`: Set to `true` or `false` depending on whether the offline PC has a CUDA-compatible GPU.
- `HF_HUB_OFFLINE=1`: You can optionally set this environment variable to strictly force the HuggingFace `transformers` library not to even attempt network requests.

