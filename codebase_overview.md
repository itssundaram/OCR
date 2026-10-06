# Mod_OCR-dev Codebase Overview

Mod_OCR-dev is a robust, modular Document OCR (Optical Character Recognition) processing system. It runs 100% offline using state-of-the-art open-source models for layout detection, text recognition, and intelligent semantic extraction.

## 🏗️ System Architecture

The project is split into two main components:
1. **Backend (`ocr_system`)**: A Python-based REST API built with **FastAPI**. It handles heavy lifting like document processing pipelines, OCR, database management, and interacting with local LLMs (Large Language Models).
2. **Frontend (`frontend`)**: A modern **React** Single Page Application bundled with **Vite** and styled with **Tailwind CSS**. It acts as the user interface to upload documents, review the extracted structured data, and track processing progress.

### Backend Structure (`ocr_system/`)
* **`api/routes.py`**: Defines all FastAPI endpoints (e.g., upload, retrieve document data, track progress, review/update detected regions, dashboard stats).
* **`app/main.py` & `config.py`**: Application entry point, server setup, CORS configuration, and environment variable management.
* **`core/pipeline.py`**: The orchestrator of the entire OCR process.
* **`core/` (Various Modules)**: Core logic modules including Layout Detection (`layout.py`), OCR Engine (`ocr_engine.py`), Preprocessing (`preprocessing.py`), LLM cleaning (`llm_cleaner.py`), and Handwriting OCR fallback (`handwriting_ocr.py`).
* **`db/`**: SQLAlchemy database configurations and models (`models.py`). Handles storage for Documents, Pages, Regions, and Tables.
* **`services/`**: High-level business logic wrappers (e.g., PDF conversion, Table processing, Checkbox detection, Extraction using LLMs).
* **`storage/`**: Local file management for handling user uploads and generating outputs.

---

## 🔄 The OCR Workflow

When a document is uploaded, it goes through an asynchronous processing pipeline detailed in `core/pipeline.py`:

1. **Upload & Initialization**: The document is received, saved to disk, and a record is created in the database. The processing task is sent to the background.
2. **Conversion**: If the file is a PDF, it is converted into images (page by page) using `PyMuPDF`.
3. **Preprocessing**: Images undergo orientation correction (rotation), blank space cropping, and deskewing.
4. **Layout Detection**: Using `surya-layout`, the system identifies distinct functional regions (e.g., Text, Table, Form, Checkbox, Signature, Title).
5. **OCR Extraction**: The text in each block is recognized using `surya-ocr`.
    * **Handwriting Fallback**: If standard OCR confidence is < 80%, a specialized handwriting model is used.
    * **Specialized Pipelines**: 
        * **Tables**: Grid structure and rows/columns are parsed out.
        * **Checkboxes**: Detected and marked as checked/unchecked.
        * **Forms**: Structured fields are extracted using local LLMs.
    * **LLM Cleaning**: Any region that still has low confidence (< 85%) is sent to the local LLM (`mistral:7b`) to contextually fix spelling and grammar.
6. **Semantic Extraction**: The fully processed document text is analyzed by the LLM to extract document-level semantic metadata (e.g., Invoice Number, Date, Total Amount).
7. **Finalization & Export**: The user can review the results on the frontend and export the finalized structural document to a new, fully reconstructed PDF.

---

## ⚙️ Configuration (`.env`)

Environment variables are configured in the `.env` file (copied from `.env.example`).

* **Database Options**:
  * **Option A**: Local SQLite (Default). Requires no configuration, creates an `ocr_system.db` file automatically.
  * **Option B**: PostgreSQL. Set `DATABASE_URL` like `postgresql://postgres:password@localhost:5432/mod_rag`.
* **Model Setup Parameters**:
  * `ALLOWED_EXTENSIONS`: File types allowed (e.g., `pdf,jpg,jpeg,png,tiff`).
  * `MAX_UPLOAD_SIZE`: Document size limit (default 100 MB).
  * `DEFAULT_DPI`: DPI used during PDF to image conversion (default 300).

---

## 🚀 How to Run the Project Deeply (Offline)

### Phase 1: Prerequisites (Internet Required Initial Setup)
1. Install [Ollama](https://ollama.com) on your system.
2. Pull the required language model for text cleaning and semantic extraction:
   ```bash
   ollama pull mistral:7b
   ```

### Phase 2: Backend Setup
Ensure you have Python 3.10+ installed. Navigate to the project root directory (`c:\Users\Sundaram\Downloads\Mod_OCR-dev\Mod_OCR-dev`):
1. **Create and Activate a Virtual Environment:**
   ```bash
   # Windows
   python -m venv venv
   .\venv\Scripts\activate
   
   # Linux/Mac
   python3 -m venv venv
   source venv/bin/activate
   ```
2. **Install Dependencies:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
3. **Set Up the Environment:**
   Copy `.env.example` to `.env`. Leave as default to use SQLite.
4. **Initialize Database:**
   ```bash
   python create_tables.py
   ```
5. **Pre-download OCR Models (Important for Offline Mode):**
   ```bash
   python -c "from huggingface_hub import snapshot_download; snapshot_download('vikp/surya_layout'); snapshot_download('vikp/surya_ocr'); snapshot_download('microsoft/trocr-large-handwritten')"
   ```

### Phase 3: Frontend Setup
Navigate to the `frontend` directory:
```bash
cd frontend
npm install
```

### Phase 4: Start the Servers
You can use the unified start script from the root directory or start them manually.

**Method A: Unified Script (Root Directory)**
```bash
# Git Bash on Windows / Linux / Mac
./start.sh
```
*This opens both servers in separate terminal windows (on Windows/Mac with GUI) or runs them in the background (on headless).*

**Method B: Manual Starting (Two Terminals)**
1. **Terminal 1 (Backend - Root Directory):**
   ```bash
   .\venv\Scripts\activate
   python -m ocr_system.app.main
   ```
   *Server runs on `http://localhost:8005`. API Docs available at `http://localhost:8005/api/v1/openapi.json`*

2. **Terminal 2 (Frontend - `frontend` Directory):**
   ```bash
   cd frontend
   npm run dev
   ```
   *Frontend app runs on `http://localhost:5173`.*
