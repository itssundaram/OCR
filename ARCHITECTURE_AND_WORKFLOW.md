# DOCINT — System Architecture & Complete Technical Workflow Specification

> **Classification:** Technical Architecture & Systems Manual  
> **Platform Version:** DocInt 1.0.0 (Offline / Air-Gapped Ready)  
> **Target Environment:** Windows 10/11 / Windows Server, Oracle 23ai Database, Local Redis 6379, NVIDIA CUDA / Multi-Core CPU  

---

## 1. Executive Summary

**DocInt** is an enterprise- and military-grade Intelligent Document Processing (IDP) platform designed to extract high-accuracy key-value pairs (KVPs), tabular structures, and handwritten annotations from structured, semi-structured, and scanned PDF/image documents.

### Key Capabilities
- **100% Air-Gapped / Offline Operation:** Zero internet connectivity required. All OCR, transformer, and Vision-Language Models (VLMs) run locally from pre-cached disk storage and local inference runtimes.
- **Sub-Second Geometric Memory (`learned_anchor`):** Autonomous template learning that remembers document geometry. On warm hits, it executes parallel micro-region OCR in under **300ms–2s** without invoking expensive large vision models.
- **Multi-Pipeline Orchestration & Consensus Voting:** Concurrently dispatches documents across multiple distinct OCR engines (Learned Anchor, Surya, Tesseract), aligning and reconciling field values through fuzzy consensus scoring and majority voting.
- **Pixel-Accurate Visual Evidence:** Automatically generates cropped image snippets for every single extracted field and table, providing instant visual verification for auditors and data operators.

---

## 2. High-Level System Architecture

```mermaid
flowchart TD
    subgraph ClientTier ["Client Tier (Frontend - React 18 + Vite)"]
        UI_Upload["Upload & Single OCR (/ocr)"]
        UI_Orch["Multi-Pipeline Orchestrator (/orchestration)"]
        UI_Results["Extraction Results & Crop Inspector (/results)"]
        UI_Dept["Department & Template Studio (/departments)"]
    end

    subgraph APITier ["API & Gateway Tier (FastAPI + Uvicorn)"]
        API_Extract["POST /api/extract/single<br/>POST /api/extract/compare"]
        API_Evidence["GET /api/evidence/crop/{asset_id}"]
        API_Events["GET /api/events/{job_id} (SSE Streaming)"]
        API_Templates["CRUD /api/templates<br/>CRUD /api/departments"]
    end

    subgraph BrokerTier ["Asynchronous Message Broker & Task Queue"]
        RedisDB[("Redis Queue (Port 6379)")]
        RQWorker["RQ Background Worker Daemon (run_worker.py)"]
    end

    subgraph OrchestratorTier ["Orchestration & Consensus Engine"]
        Orchestrator["Pipeline Orchestrator (orchestrator.py)"]
        ConsensusEngine["Consensus & Voting Engine (consensus.py)"]
        AssetService["Visual Asset & Crop Service (asset_service.py)"]
    end

    subgraph PipelineEngines ["Pipeline Execution Engines"]
        P_Learned["Learned Geometric Pipeline<br/>(learned_anchor_pipeline.py)"]
        P_Surya["Surya OCR Pipeline<br/>(surya_pipeline.py)"]
        P_Tesseract["Tesseract OCR Pipeline<br/>(tesseract_pipeline.py)"]
        P_Qwen["Direct VLM Pipeline<br/>(Qwen2.5-VL:3B)"]
    end

    subgraph OfflineModels ["Offline Model Vault (backend/data/models/)"]
        M_Qwen["Ollama Qwen2.5-VL:3B"]
        M_Tess["Tesseract 5.4.0 (eng.traineddata)"]
        M_Surya["Surya (det3, rec2, layout3)"]
        M_TATR["TATR (table detection + structure)"]
        M_TrOCR["TrOCR (handwritten large)"]
    end

    subgraph DatabaseTier ["Persistence Tier (Oracle 23ai Database)"]
        OracleDB[("Oracle 23ai FREEPDB1<br/>(DATA_OCR Schema)")]
    end

    %% Wiring
    ClientTier -->|REST API & SSE| APITier
    APITier -->|Enqueue ProcessingJob| RedisDB
    RedisDB -->|Dequeue Job| RQWorker
    RQWorker --> Orchestrator
    Orchestrator --> PipelineEngines
    Orchestrator --> ConsensusEngine
    Orchestrator --> AssetService
    PipelineEngines --> OfflineModels
    Orchestrator -->|Persist Results & Events| OracleDB
    AssetService -->|Save Crops & Thumbnails| FileSystem[("Local File Vault (/data/crops, /data/documents)")]
    APITier -->|Query Status & Results| OracleDB
```

---

## 3. Deep Dive: Machine Learning & OCR Models

DocInt uses a specialized suite of localized machine learning and neural network models:

| Model Name | Framework / Architecture | Disk Location | Role in Platform | Latency / Device Profile |
| :--- | :--- | :--- | :--- | :--- |
| **Qwen2.5-VL:3B** | Multimodal Vision-Language Transformer (Alibaba / Ollama) | Local Ollama Model Storage (`localhost:11434`) | High-level semantic reasoning, cold-path layout discovery, noisy document rectification | GPU (CUDA 12/13): ~1.2s–2.5s<br/>CPU: ~8s–15s |
| **Tesseract 5.4.0** | LSTM Neural Network OCR (Google / UB Mannheim) | `C:\Program Files\Tesseract-OCR\` | High-speed printed text extraction, word-level confidence and bounding-box coordinates | CPU: ~150ms–400ms/page |
| **Surya OCR** | SegFormer / Vision Transformer (`vikp/surya_*`) | `data/models/huggingface/hub/` | Line-level polygon detection, complex reading order analysis, multi-column document extraction | CPU: ~2.5s–5.0s/page<br/>GPU: ~400ms/page |
| **Table Transformer (TATR)** | DETR (DEtection TRansformer - Microsoft) | `data/models/huggingface/hub/` (`models--microsoft--table-transformer-*`) | Deep tabular extraction: table boundary detection and table row/column structure recognition | CPU: ~1.5s–3.0s/page |
| **TrOCR Large** | Vision-Encoder-Decoder (Microsoft) | `data/models/huggingface/hub/` (`models--microsoft--trocr-large-handwritten`) | Selective handwritten cursive text recognition on low-confidence fallback fields | CPU: ~1.0s–2.0s per crop |

---

## 4. Pipeline Architectures

### 4.1. Learned Geometric Anchor Pipeline (`learned_anchor`)
The flagship sub-second pipeline designed for repetitive documents (e.g., military vehicle inspection reports, logistics manifests, invoices, certificates).

```mermaid
flowchart TD
    Start["Receive Document Page"] --> CheckMemory{"Learned Anchors exist in Template.template_json?"}

    subgraph ColdPath ["Cold Learning Path (First Execution / Fallback)"]
        CheckMemory -- "No (Cold Path)" --> FullOCR["Full Page Text & Word Extraction"]
        FullOCR --> VLMInfer["Qwen 2.5-VL Inference with Structured Schema Prompt"]
        VLMInfer --> ParseKVP["Extract Candidate KVPs & Values"]
        ParseKVP --> MapCoords["Locate Exact Word Bounding Boxes for Keys & Values"]
        MapCoords --> SaveAnchors["Persist Learned Coordinates to TEMPLATES.template_json"]
        SaveAnchors --> ReturnCold["Return PipelineResult"]
    end

    subgraph WarmPath ["Warm Geometric Micro-OCR Path (Sub-Second)"]
        CheckMemory -- "Yes (Warm Path)" --> Preprocess["Image Preprocessing (Grayscale + Autocontrast cutoff=1)"]
        Preprocess --> LabelLocate["Dynamic Label Proximity Search on Target Page"]
        LabelLocate --> FoundLabel{"Field Label Found on Document?"}
        
        FoundLabel -- "Yes (Dynamic Anchor)" --> LineScan["Inspect Same Line to Right of Label for Value Regex"]
        LineScan --> FoundVal{"Value matches Field Regex?"}
        FoundVal -- "Yes" --> DirectSnap["Lock Direct Value & Tight Bounding Box (+4px padding)"]
        FoundVal -- "No" --> CandCrop["Crop Candidate Horizontal Region (lx2 to lx2+400px)"]

        FoundLabel -- "No (Static Fallback)" --> PadCrop["Fetch Template Coordinates with Expanded Padding (±35px H, ±15px V)"]
        
        CandCrop --> MicroOCR["Parallel ThreadPool Micro-OCR (Tesseract --psm 6)"]
        PadCrop --> MicroOCR
        MicroOCR --> ValueRegex["Strict Regex Extraction & Blacklist Noise Filtering"]
        ValueRegex --> WordSnap["Calculate Word-Level Relative Coordinates & Snap Bbox"]
        DirectSnap --> AssembleWarm["Assemble FieldResults & Save Visual Crop Assets"]
        WordSnap --> AssembleWarm
    end

    AssembleWarm --> OutputResult["Return Complete Extraction Result"]
    ReturnCold --> OutputResult
```

#### Key Innovations in `learned_anchor`:
1. **Dynamic Label-Anchored Proximity Localization:**
   Rather than assuming static pixel coordinates across vehicle variants (e.g. Maruti Gypsy has 17 header rows, Ashok Leyland Stallion has 24 header rows, TATA 2.5 Ton has 20 header rows), the pipeline dynamically scans for the key token (`Engine No:`, `Chassis No:`, `BA No:`) and anchors relative to where the label actually appears on *this* document.
2. **Preprocessing Filter:**
   Runs `ImageOps.autocontrast(gray, cutoff=1)` to eliminate scanner background grayness, faint carbon paper artifacts, and shadow borders.
3. **Regex Value Snapping:**
   Uses domain regex (military BA number `\d{2}[A-Z]\s*\d{5,7}[A-Z]?`, engine serials `[A-Z0-9/\-]{6,16}`, chassis VINs) to filter extraneous text. The bounding box is automatically snapped strictly to the words matching the regex, discarding adjacent labels and neighboring rows.

---

### 4.2. Surya OCR Pipeline (`surya`)
- **Stage 1 (Layout Analysis):** Runs `surya_layout3` to separate headers, footers, paragraphs, and tables.
- **Stage 2 (Line Detection):** Runs `surya_det3` to locate polygon bounding boxes for every single line.
- **Stage 3 (Reading Order & Recognition):** Re-orders lines into human reading flow and recognizes text using `surya_rec2`.
- **Stage 4 (Semantic Field Matching):** Matches template field names against clustered text blocks via Levenshtein distance and proximity indexing.

---

### 4.3. Tesseract OCR Pipeline (`tesseract`)
- **Stage 1 (Page Rendering):** Converts PDF vector pages to 150/300 DPI high-resolution bitmaps.
- **Stage 2 (Layout & Word Extraction):** Runs Tesseract 5.4 with `--psm 1` or `--psm 3` (automatic page segmentation) returning word-level bounding boxes and confidence scores.
- **Stage 3 (Table Extraction):** Dispatches detected tabular regions to Microsoft Table Transformer (`TATR`) for structural row/column reconstruction.

---

### 4.4. Multi-Pipeline Comparison & Consensus Engine
Dispatched when users execute documents through the **Multi-Pipeline Orchestrator** (`/orchestration`).

```mermaid
sequenceDiagram
    autonumber
    actor User as User / Client
    participant API as FastAPI Router
    participant Orch as Pipeline Orchestrator
    participant Pool as ThreadPoolExecutor
    participant Consensus as Consensus & Voting Engine
    participant DB as Oracle Database

    User->>API: POST /api/extract/compare (job_id, pipelines=['learned_anchor', 'tesseract', 'surya'])
    API->>Orch: run_pipeline_comparison_job(job_id, pipelines)
    Orch->>DB: Record PIPELINE_STARTED
    par Concurrent Execution
        Orch->>Pool: learned_anchor.run(pages)
        Orch->>Pool: tesseract.run(pages)
        Orch->>Pool: surya.run(pages)
    end
    Pool-->>Orch: Return PipelineResults [R1, R2, R3]
    Orch->>Consensus: compare_pipeline_results(runs, template_fields)
    Note over Consensus: Normalize values (case, punctuation, whitespace)<br/>Run fuzzy distance matching<br/>Perform majority voting & agreement scoring
    Consensus-->>Orch: Final Consolidated Fields + Consensus Rate (%)
    Orch->>DB: Persist PipelineRuns, FieldExtractions, ProcessingResults
    Orch-->>API: Stream completion event (SSE)
    API-->>User: Render Multi-Pipeline Comparison Matrix
```

---

## 5. End-to-End Processing Workflow

### Workflow 1: Single Document Upload & Extraction (`/ocr`)

```mermaid
sequenceDiagram
    autonumber
    actor User as Operator / Analyst
    participant UI as Vite Web Client (/ocr)
    participant API as FastAPI Backend
    participant Worker as RQ Worker (Windows)
    participant Oracle as Oracle 23ai DB
    participant Vault as File Storage Vault

    User->>UI: Select Department & Template, Drop PDF/Image
    UI->>API: POST /api/extract/single (file, template_id, pipeline_name)
    API->>Vault: Save file to data/documents/{uuid}.pdf
    API->>Oracle: INSERT INTO DOCUMENTS (status='PENDING')
    API->>Oracle: INSERT INTO PROCESSING_JOBS (status='QUEUED')
    API->>Worker: Enqueue task 'run_single_pipeline_job' (job_id)
    API-->>UI: Return HTTP 202 {job_id, status: 'QUEUED'}
    
    loop Real-time Progress (SSE / Polling)
        UI->>API: GET /api/events/{job_id}
        API-->>UI: Stream: PIPELINE_STARTED -> OCR_PROCESSING -> COMPLETED
    end

    Worker->>Oracle: UPDATE PROCESSING_JOBS SET status='PROCESSING'
    Worker->>Worker: Execute Chosen Pipeline (e.g. learned_anchor)
    Worker->>Vault: Crop visual bounding boxes -> data/crops/{job_id}_{field}.png
    Worker->>Oracle: INSERT INTO ASSETS (asset_type='crop_image')
    Worker->>Oracle: INSERT INTO FIELD_EXTRACTIONS (is_final=1, bbox_json, crop_asset_id)
    Worker->>Oracle: UPSERT INTO PROCESSING_RESULTS (extracted_json, confidence_json)
    Worker->>Oracle: UPDATE PROCESSING_JOBS SET status='COMPLETED'
    
    UI->>API: GET /api/processing/jobs/{job_id}/results
    API-->>UI: Full Payload with Resolved Values, Confidences & Crop URLs
    UI-->>User: Render Verified Results, Interactive PDF Bounding Boxes & Crops
```

---

## 6. Oracle 23ai Database Schema & Entity Relationships

```mermaid
erDiagram
    DEPARTMENTS ||--o{ TEMPLATES : "owns"
    DEPARTMENTS ||--o{ DEPARTMENT_URLS : "hosts"
    TEMPLATES ||--o{ TEMPLATE_FIELDS : "defines"
    TEMPLATES ||--o{ DOCUMENTS : "classifies"
    TEMPLATES ||--o{ PROCESSING_JOBS : "executes"
    DOCUMENTS ||--o{ PROCESSING_JOBS : "undergoes"
    DOCUMENTS ||--o{ DOCUMENT_PAGES : "contains"
    PROCESSING_JOBS ||--o{ PIPELINE_RUNS : "dispatches"
    PROCESSING_JOBS ||--o{ FIELD_EXTRACTIONS : "extracts"
    PROCESSING_JOBS ||--o{ TABLE_EXTRACTIONS : "extracts"
    PROCESSING_JOBS ||--o| PROCESSING_RESULTS : "summarizes"
    PROCESSING_JOBS ||--o{ PROCESSING_EVENTS : "logs"

    DEPARTMENTS {
        NUMBER id PK
        VARCHAR2 name
        VARCHAR2 slug UK
        VARCHAR2 description
        NUMBER is_active
        TIMESTAMP created_at
    }

    TEMPLATES {
        NUMBER id PK
        NUMBER department_id FK
        VARCHAR2 code
        NUMBER version
        CLOB template_json
        CLOB extraction_instructions
        NUMBER is_active
    }

    TEMPLATE_FIELDS {
        NUMBER id PK
        NUMBER template_id FK
        VARCHAR2 name
        VARCHAR2 label
        VARCHAR2 field_type
        NUMBER is_required
        NUMBER confidence_threshold
        CLOB validation_rules
    }

    DOCUMENTS {
        VARCHAR2 id PK "UUID"
        VARCHAR2 original_filename
        VARCHAR2 file_path
        VARCHAR2 file_type
        NUMBER file_size_bytes
        NUMBER template_id FK
        VARCHAR2 status "CHECK (status IN ('PENDING', 'QUEUED', 'PROCESSING', 'COMPLETED', 'WARNING', 'FAILED'))"
    }

    PROCESSING_JOBS {
        VARCHAR2 id PK "UUID"
        VARCHAR2 document_id FK
        NUMBER template_id FK
        VARCHAR2 status "CHECK (status IN ('QUEUED', 'PROCESSING', 'OCR_PROCESSING', 'AI_PROCESSING', 'VALIDATING', 'COMPLETED', 'WARNING', 'FAILED'))"
        NUMBER overall_confidence
        NUMBER duration_ms
        TIMESTAMP started_at
        TIMESTAMP completed_at
    }

    FIELD_EXTRACTIONS {
        VARCHAR2 id PK "UUID"
        VARCHAR2 job_id FK
        VARCHAR2 pipeline_run_id FK
        VARCHAR2 field_name
        CLOB field_value
        NUMBER confidence
        VARCHAR2 extraction_method
        VARCHAR2 fallback_method "needs_review"
        CLOB bbox_json
        NUMBER is_final
    }

    PROCESSING_RESULTS {
        NUMBER id PK
        VARCHAR2 job_id FK UK
        CLOB extracted_json
        CLOB confidence_json
        CLOB ocr_metadata_json
    }

    ASSETS {
        VARCHAR2 id PK "UUID"
        VARCHAR2 asset_type "crop_image | thumbnail"
        VARCHAR2 file_path
        VARCHAR2 mime_type
        TIMESTAMP created_at
    }
```

---

## 7. Storage & File System Layout

```
OCR_final/
├── backend/
│   ├── app/
│   │   ├── ai/                        # AI & LLM Orchestration
│   │   │   ├── consensus.py           # Multi-pipeline agreement & voting logic
│   │   │   ├── ollama.py              # Ollama API client (Qwen2.5-VL:3B)
│   │   │   ├── orchestrator.py        # Central AI inference dispatcher
│   │   │   └── prompt_builder.py      # Schema-driven dynamic prompt synthesis
│   │   ├── api/routes/                # FastAPI Endpoints
│   │   │   ├── extract.py             # Ingestion & pipeline dispatch
│   │   │   ├── evidence.py            # Crop image rendering & retrieval
│   │   │   ├── events.py              # Server-Sent Events (SSE) progress streams
│   │   │   ├── processing.py          # Job status & result querying
│   │   │   └── templates.py           # Template CRUD & geometry definition
│   │   ├── db/                        # Oracle Database Layer
│   │   │   ├── database.py            # SQLAlchemy engine, session maker, connection pool
│   │   │   └── models/core.py         # SQLAlchemy ORM entity models
│   │   ├── ocr/                       # Low-level OCR & Preprocessing Engines
│   │   │   ├── tesseract.py           # Pytesseract wrapper & word extractor
│   │   │   ├── tatr_engine.py         # Microsoft Table Transformer integration
│   │   │   └── handwriting_ocr.py     # Microsoft TrOCR handwriting fallback
│   │   ├── pipelines/                 # Pipeline Implementations
│   │   │   ├── base.py                # Abstract OCRPipeline & FieldResult interfaces
│   │   │   ├── registry.py            # Dynamic runtime pipeline discovery registry
│   │   │   ├── learned_anchor_pipeline.py # Sub-second geometric anchor pipeline
│   │   │   ├── surya_pipeline.py      # Surya OCR pipeline
│   │   │   └── tesseract_pipeline.py  # Tesseract OCR pipeline
│   │   └── services/
│   │       ├── orchestrator.py        # Single and comparison job runners
│   │       └── asset_service.py       # Visual crop clipping & filesystem storage
│   ├── data/
│   │   ├── crops/                     # Saved PNG crops of extracted field regions
│   │   ├── documents/                 # Uploaded PDF and image files
│   │   └── models/                    # Offline Pre-cached ML Models
│   │       └── huggingface/hub/       # TrOCR, Surya, TATR model weights & snapshots
│   ├── run_worker.py                  # RQ Worker daemon for background jobs
│   └── venv/                          # Active Python virtual environment
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── OcrPage.jsx            # Single OCR upload & live execution view
│   │   │   ├── OrchestrationPage.jsx  # Multi-pipeline consensus matrix & disagreement view
│   │   │   └── ExtractionResultsPage.jsx # Historical extractions and crop inspector
│   │   └── components/
│   │       ├── ExtractionResultView.jsx # Field viewer with source crop drawer
│   │       └── PdfViewer.jsx          # PDF.js renderer with interactive bounding boxes
│   └── public/
│       └── pdf.worker.min.js          # Offline PDF.js web worker bundle
└── ARCHITECTURE_AND_WORKFLOW.md        # This specification document
```

---

## 8. Operational Quick Reference

### Starting the Platform
```powershell
# 1. Start Oracle Database & Local Redis
# (Ensure Oracle FREEPDB1 is up on port 1521, Redis on port 6379)

# 2. Start the Backend API (from backend/ directory)
cd backend
python -m uvicorn app.main:app --reload --port 8000

# 3. Start the Background Task Worker (from backend/ directory)
cd backend
python run_worker.py

# 4. Start the Frontend Development Server (from frontend/ directory)
cd frontend
npm run dev
```

### Accessing the Web Application
- **Main Platform:** `http://localhost:5173/`
- **Single OCR & Fast Geometric Path:** `http://localhost:5173/ocr`
- **Multi-Pipeline Orchestrator:** `http://localhost:5173/orchestration`
- **Interactive OpenAPI Documentation:** `http://localhost:8000/docs`
