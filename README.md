# DOCINT: Intelligent Document Extraction Platform

**DOCINT (OCR_final)** is a modern, fully offline-capable, template-driven intelligent document processing platform. It leverages a hybrid OCR engine and local Vision/Large Language Models (LLMs) to automatically extract, structure, and store data from physical or digital documents (PDFs/Images) according to dynamic user-defined JSON schemas.

---

## 🚀 The User Journey

The system is designed to be highly intuitive and template-driven:

### 1. Template Creation (The Wizard)

Before extracting data, users define *what* they want to extract.

- **Step 1: Department**: The user creates or selects a logical department (e.g., "Finance").
- **Step 2: Template & Fields**: The user creates a template (e.g., "Invoice") linked directly to the department. They use a dynamic field builder to specify exact data points (e.g., `invoice_number` as Text, `date` as Date).
- **Step 3: Endpoint Ready**: Once saved, a dedicated extraction endpoint is automatically mapped to this template (e.g., `/api/v1/extract/finance/invoice`).

### 2. Document Upload & Processing

- The user navigates to the "Upload" page, selects their desired Template from the dropdown, and uploads a PDF.
- The document is securely stored locally, and an asynchronous processing job is placed into a **Redis Queue (RQ)**. (By default, only the 1st page is processed for maximum speed).

### 3. Review Extraction

- The user views the document in the dashboard. The frontend polls the backend until the processing job transitions from `QUEUED` ➔ `PROCESSING` ➔ `COMPLETED`.
- The user can view the final perfectly structured JSON side-by-side with the document.

---

## 🧠 Core Architecture & Pipeline

The project is split into two main architectural pillars:

### 1. Frontend (React + Vite)

A fast, modern SPA built with React, Vite, Tailwind CSS, and Lucide Icons. It handles the template builder wizard, document uploads, and live status polling.

### 2. Backend API (FastAPI) & Workers (Redis RQ)

A modular Python FastAPI backend that provides RESTful endpoints and handles Oracle database transactions (via SQLAlchemy). Heavy lifting (document processing) is offloaded to background worker processes managed by Redis RQ to ensure the API remains blazing fast.

### The Extraction Pipeline (The Brains)

When a document is uploaded, it runs through one of two configurable pipelines:

**Path A: Direct Vision LLM (Fast Path - Recommended)**
- When `DIRECT_LLM_CALL=True`, the system skips slow OCR completely.
- Documents are instantly rendered as optimized thumbnails (72 DPI).
- The raw images are passed directly to a local Vision LLM (e.g., `qwen2.5vl:3b`) which extracts the structured JSON visually. This results in processing times of ~3-5 seconds per page.

**Path B: Hybrid Legacy OCR (Fallback)**
- When `DIRECT_LLM_CALL=False`, the system falls back to traditional OCR.
- **Digital PDFs**: `PyMuPDF` extracts pure digital text instantly.
- **Scanned PDFs**: `Tesseract CLI` acts as a robust fallback for scanned documents (rendered at 144+ DPI).
- **Tables**: `img2table` intercepts tables and perfectly parses them into Markdown grids.
- The stitched text (and Markdown tables) are passed to a standard text LLM (e.g., `llama3.2`).

---

## 🛠️ Technology Stack

- **Frontend**: React, Vite, Tailwind CSS
- **Backend**: Python 3.11, FastAPI, Pydantic
- **Workers**: Redis, Python RQ (Redis Queue)
- **Database**: Oracle DB (`python-oracledb` thin mode)
- **OCR / Vision**: PyMuPDF (`fitz`), Tesseract, `img2table`
- **AI / LLM**: Ollama (Local hosting)

---

## 💻 How to Run Locally

### Prerequisites

1. **Oracle Database**: Running and accessible.
2. **Redis**: Running locally on port `6379`.
3. **Ollama**: Running locally on port `11434` with your target model pulled (e.g., `ollama run qwen2.5vl:3b`).
4. **Tesseract**: Installed and added to your system PATH (only required if not using the Fast Path).

### 1. Database & Environment Setup

1. Run the `oracle_schema.sql` script in your Oracle database to create all required tables, sequences, and constraints.
2. In the `backend/` directory, copy `.env.example` to `.env` and fill in your Oracle/Redis credentials.
3. In the `frontend/` directory, copy `.env.example` to `.env`.

### 2. Start the Backend API & Worker

Open two separate terminals in the `backend/` directory:

**Terminal 1 (API Server):**
```bash
.\venv\Scripts\activate
python -m uvicorn app.main:app --reload
```
*API runs on http://localhost:8000*

**Terminal 2 (Redis Worker):**
```bash
.\venv\Scripts\activate
python run_worker.py
```
*Worker listens to the Redis queue for document jobs*

### 3. Start the React Frontend

Open a terminal in the `frontend/` directory:
```bash
npm install
npm run dev
```
*UI runs on http://localhost:5173*

---

## 🔒 Strict Offline Deployment Guide

This project is specifically designed to function in highly secure, air-gapped offline environments.

To deploy offline:

1. Ensure the target machine has **Python 3.11**, **Redis**, and **Ollama** installed.
2. Transfer this entire `OCR_final` directory to the offline machine via secure USB/Network.
3. The `backend/offline_package` folder contains all pre-downloaded `.whl` dependencies.
4. Run the following command inside `backend` to install entirely from the local cache:

```bash
python -m venv venv
.\venv\Scripts\activate
python -m pip install --no-index --find-links=offline_package -r requirements.txt
```
