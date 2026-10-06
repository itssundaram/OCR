# 🚀 Surya OCR Strategy — Implementation Plan

## Current State (Your Codebase)

| Component | Current Tool | Target Tool |
|-----------|-------------|-------------|
| OCR Engine | Tesseract (subprocess CLI) | **surya-ocr** |
| Layout Detection | ❌ None | **surya-layout** |
| Table Extraction | img2table + Tesseract | img2table + **surya-ocr** (or keep) |
| Handwriting Fallback | ❌ None | **TrOCR** (microsoft/trocr-large-handwritten) |
| Image Preprocessing | Minimal (contrast/sharpness) | Add deskew + orientation correction |
| Text Source for LLM | Tesseract raw text | Surya-extracted text (much higher quality) |

---

## What the Overview Codebase Does (Target Strategy)

The Mod_OCR-dev strategy from `codebase_overview.md` follows this 7-step pipeline:

```
Upload → PDF→Image Conversion → Preprocessing → Layout Detection → OCR → LLM Cleaning → Semantic Extraction
```

Key innovations over your current code:
1. **Surya Layout** — identifies regions (Text, Table, Form, Checkbox, Signature, Title) before OCR
2. **Surya OCR** — runs OCR *per region*, not on the full page blindly
3. **Handwriting Fallback** — if confidence < 80%, switch to TrOCR handwriting model
4. **Confidence-gated LLM Cleaning** — if confidence still < 85% after OCR, send region to LLM (mistral/ollama) to clean it up
5. **Specialized Pipelines per region type** — Tables parsed structurally, Checkboxes detected, Forms extracted via LLM

---

## What We Will Keep Unchanged

- ✅ FastAPI backend structure
- ✅ Oracle DB + SQLAlchemy models
- ✅ Redis RQ worker queue
- ✅ `image_extractor.py` (PyMuPDF → PIL Images pipeline)
- ✅ All AI/LLM extraction (`ai/orchestrator.py`, `ai/ollama.py`, `prompt_builder.py`, etc.)
- ✅ All API routes / schemas
- ✅ Frontend (React + Vite)
- ✅ `OCRResult / OCRPage / OCRLine / OCRTable` base data structures

---

## Files to Create / Modify

### 🆕 New Files (Backend)

| File | Purpose |
|------|---------|
| `app/ocr/surya_ocr_engine.py` | Surya OCR engine implementing `OCREngine` ABC |
| `app/ocr/surya_layout_engine.py` | Surya layout detection — returns typed regions |
| `app/ocr/preprocessing.py` | Image preprocessing: deskew, orientation fix, crop |
| `app/ocr/handwriting_ocr.py` | TrOCR handwriting fallback engine |
| `app/ocr/region_processor.py` | Per-region dispatcher: routes each layout region to the right pipeline |

### ✏️ Modified Files (Backend)

| File | Change |
|------|--------|
| `app/services/extractor.py` | Replace `_process_single_page()` to use Layout → Surya OCR pipeline |
| `app/ocr/orchestrator.py` | Add Surya engine + layout; keep Tesseract as emergency fallback |
| `app/ocr/img2table_engine.py` | Swap Tesseract OCR inside img2table for Surya (optional, can keep Tess here) |
| `app/core/config.py` | Add Surya config flags (`SURYA_LAYOUT_ENABLED`, `HANDWRITING_FALLBACK_ENABLED`, etc.) |
| `backend/requirements.txt` | Add `surya-ocr`, `surya-layout`, `transformers`, `torch` |
| `backend/.env` | Add new OCR strategy env vars |

---

## Phase-by-Phase Implementation Plan

---

### Phase 1 — Add Dependencies & Config (No Breaking Changes)
**Risk: Zero** — purely additive

**Step 1.1 — `requirements.txt`**
Add:
```
surya-ocr>=0.6.0
# surya-layout is bundled with surya-ocr
transformers>=4.40.0    # for TrOCR handwriting fallback
torch>=2.1.0            # CPU/GPU tensor backend
timm>=0.9.0             # image models (dependency of surya)
```

> [!WARNING]
> `surya-ocr` requires `torch`. On a CPU-only machine this will be large (~800MB). It will download models from HuggingFace on first run. Make sure to pre-download offline.

**Step 1.2 — `app/core/config.py`**
Add these new settings:
```python
# ── Surya OCR ───────────────────────────────────────────────────────────────
OCR_STRATEGY: Literal["tesseract_only", "surya", "surya_with_fallback"] = "surya_with_fallback"
SURYA_LAYOUT_ENABLED: bool = True
SURYA_LAYOUT_CONFIDENCE_THRESHOLD: float = 0.3   # min score to keep a region
HANDWRITING_FALLBACK_ENABLED: bool = True
HANDWRITING_FALLBACK_THRESHOLD: float = 0.80     # if OCR confidence < this → use TrOCR
LLM_CLEANING_ENABLED: bool = True
LLM_CLEANING_THRESHOLD: float = 0.85             # if still low conf → LLM cleans text
SURYA_DEVICE: str = "cpu"                         # "cuda" if GPU available
```

**Step 1.3 — `.env`**
```ini
OCR_STRATEGY=surya_with_fallback
SURYA_LAYOUT_ENABLED=true
HANDWRITING_FALLBACK_ENABLED=true
HANDWRITING_FALLBACK_THRESHOLD=0.80
LLM_CLEANING_ENABLED=true
LLM_CLEANING_THRESHOLD=0.85
SURYA_DEVICE=cpu
```

---

### Phase 2 — Image Preprocessing Module
**File:** `app/ocr/preprocessing.py`

Implements what the overview calls "orientation correction, blank space cropping, deskewing":

```python
# Functions to implement:
def correct_orientation(img: Image.Image) -> Image.Image:
    """Detect and fix page rotation using Surya's orientation model."""

def deskew_image(img: Image.Image) -> Image.Image:
    """Fix minor skew angles using Hough line transform (OpenCV)."""

def crop_blank_borders(img: Image.Image, threshold=10) -> Image.Image:
    """Remove large white/blank margins from scanned pages."""

def preprocess_page(img: Image.Image) -> Image.Image:
    """Run full preprocessing pipeline: orient → deskew → crop."""
```

> [!NOTE]
> Surya ships a built-in `surya.detection` orientation model. For deskew we'll use `opencv-python-headless` which is lightweight.

---

### Phase 3 — Surya Layout Detection Engine
**File:** `app/ocr/surya_layout_engine.py`

This is the most impactful change. Instead of OCR on the full page, we first detect **what type of content** is where.

```python
# Region types from surya-layout:
LAYOUT_REGION_TYPES = [
    "Text", "Title", "Table", "Figure", "Form", 
    "Checkbox", "Signature", "Caption", "Footnote"
]

@dataclass
class LayoutRegion:
    region_type: str       # e.g., "Text", "Table", "Checkbox"
    bbox: BoundingBox
    confidence: float
    cropped_image: Image.Image   # the cropped region image

def detect_layout(image: Image.Image) -> list[LayoutRegion]:
    """Run surya-layout on a full page image, return sorted regions."""
```

**How it plugs in:** `extractor.py` calls `detect_layout()` per page, then routes each region to the correct pipeline (Phase 4).

---

### Phase 4 — Surya OCR Engine
**File:** `app/ocr/surya_ocr_engine.py`

Replaces `TesseractOCREngine`. Implements the `OCREngine` ABC:

```python
class SuryaOCREngine(OCREngine):
    """
    Uses surya-ocr for text recognition.
    Can process full-page images OR cropped region images.
    Returns per-line text + per-word confidence scores.
    """
    def load_model(self) -> None:
        # Load surya recognition model once, keep in memory (singleton)
        
    def process_images(self, images: list[Image.Image]) -> OCRResult:
        # Process full pages (backward compatible path)
        
    def process_region(self, region_image: Image.Image) -> tuple[list[OCRLine], float]:
        # Process a single cropped region, return (lines, avg_confidence)
```

**Key advantage:** Surya produces real word-level confidence scores (unlike Tesseract's page-level heuristics), making the handwriting fallback threshold meaningful.

---

### Phase 5 — Handwriting OCR Fallback
**File:** `app/ocr/handwriting_ocr.py`

For regions where Surya confidence < `HANDWRITING_FALLBACK_THRESHOLD`:

```python
class HandwritingOCREngine:
    """
    Uses microsoft/trocr-large-handwritten via HuggingFace transformers.
    Loaded lazily on first use (high memory, ~1.5GB).
    """
    MODEL_ID = "microsoft/trocr-large-handwritten"
    
    def process_region(self, region_image: Image.Image) -> tuple[str, float]:
        """Returns (text, confidence)"""
```

> [!IMPORTANT]
> TrOCR is loaded **lazily** — only when actually needed. This avoids ~1.5GB memory usage on docs that have no handwriting.

---

### Phase 6 — Per-Region Dispatcher
**File:** `app/ocr/region_processor.py`

The brain of the new pipeline — routes each `LayoutRegion` to the correct sub-pipeline:

```python
def process_region(region: LayoutRegion, surya_engine, handwriting_engine, config) -> OCRRegionResult:
    match region.region_type:
        case "Text" | "Title" | "Caption" | "Footnote":
            return _process_text_region(region, surya_engine, handwriting_engine, config)
        
        case "Table":
            return _process_table_region(region, surya_engine)
            # Uses existing img2table logic + surya for cell text
        
        case "Form":
            return _process_form_region(region, surya_engine)
            # Extract key:value pairs
        
        case "Checkbox":
            return _process_checkbox_region(region)
            # Vision-based checked/unchecked detection
        
        case "Figure" | "Signature":
            return _process_figure_region(region)
            # Annotate presence, don't OCR

def _process_text_region(region, surya_engine, handwriting_engine, config):
    lines, confidence = surya_engine.process_region(region.cropped_image)
    
    # Handwriting fallback
    if confidence < config.HANDWRITING_FALLBACK_THRESHOLD and config.HANDWRITING_FALLBACK_ENABLED:
        text, hw_conf = handwriting_engine.process_region(region.cropped_image)
        # use whichever confidence is higher
    
    # LLM cleaning fallback (if still low confidence)
    if confidence < config.LLM_CLEANING_THRESHOLD and config.LLM_CLEANING_ENABLED:
        text = _llm_clean_text(text)  # Ollama call for contextual correction
    
    return OCRRegionResult(lines=lines, confidence=confidence, region_type=region.region_type)
```

---

### Phase 7 — Wire Everything into `extractor.py`
**File:** `app/services/extractor.py`

Replace the `_process_single_page()` function:

```python
# OLD: Tesseract on full page
def _process_single_page(page_index, img, native_text):
    # → Tesseract, img2table

# NEW: Layout → Surya OCR → Region-specific pipelines
def _process_single_page_surya(page_index, img, native_text, config):
    # 1. Preprocess
    img = preprocess_page(img)
    
    # 2. Layout Detection
    regions = detect_layout(img)
    
    # 3. Per-region OCR
    lines = []
    tables = []
    for region in regions:
        result = process_region(region, surya_engine, handwriting_engine, config)
        lines.extend(result.lines)
        if result.table:
            tables.append(result.table)
    
    # 4. Assemble OCRPage (same structure as before → AI pipeline unchanged)
    return page_index, lines, tables, is_native, avg_conf, "surya", img.width, img.height
```

The `if settings.OCR_STRATEGY == "surya_with_fallback":` branch selects the new path; `"tesseract_only"` keeps the old path.

---

### Phase 8 — Update `orchestrator.py`
**File:** `app/ocr/orchestrator.py`

```python
class OCROrchestrator:
    def __init__(self):
        self._surya_engine: SuryaOCREngine | None = None
        self._tesseract_engine: TesseractOCREngine | None = None  # keep as fallback
    
    def _get_engine(self) -> OCREngine:
        if settings.OCR_STRATEGY in ("surya", "surya_with_fallback"):
            return self._get_surya()
        return self._get_tesseract()
```

---

## Data Flow (New vs Old)

### Old Flow
```
Page Image → Tesseract (full page) → OCRLines → LLM
                    ↓
             img2table → OCRTable → LLM
```

### New Flow
```
Page Image 
  → Preprocessing (deskew, orient, crop)
  → Surya Layout (detect regions)
      → Text/Title regions  → Surya OCR
                                  ↓ conf < 0.80?
                               TrOCR Handwriting
                                  ↓ conf < 0.85?
                               Ollama LLM Cleaning
      → Table regions       → img2table + Surya OCR cells
      → Form regions        → Surya OCR + key:value parser
      → Checkbox regions    → Vision-based binary detection
      → Figure/Signature    → Annotate only
  → Assemble OCRPage (same interface as before)
  → LLM Extraction (unchanged)
```

---

## Implementation Order (Recommended)

| Order | Task | Files | Time Estimate |
|-------|------|-------|---------------|
| 1 | Add deps & config flags | `requirements.txt`, `config.py`, `.env` | 30 min |
| 2 | Preprocessing module | `preprocessing.py` | 1 hr |
| 3 | Surya OCR engine | `surya_ocr_engine.py` | 1.5 hr |
| 4 | Surya Layout engine | `surya_layout_engine.py` | 1 hr |
| 5 | Handwriting fallback | `handwriting_ocr.py` | 45 min |
| 6 | Region processor | `region_processor.py` | 2 hr |
| 7 | Wire into extractor | `extractor.py` | 1 hr |
| 8 | Update orchestrator | `orchestrator.py` | 30 min |
| 9 | Test & tune thresholds | - | 1-2 hr |

**Total: ~10 hours of focused implementation**

---

## Model Download Commands (Pre-download for Offline)

```bash
python -c "
from huggingface_hub import snapshot_download
snapshot_download('vikp/surya_layout')
snapshot_download('vikp/surya_ocr')
snapshot_download('microsoft/trocr-large-handwritten')
"
```

---

## Key Config Knobs After Implementation

```ini
# In .env — tune these for your documents:

# Main strategy switch:
OCR_STRATEGY=surya_with_fallback   # or: surya | tesseract_only

# When to switch from Surya → Handwriting model:
HANDWRITING_FALLBACK_THRESHOLD=0.80   # lower = more aggressive fallback

# When to call Ollama to clean up OCR text:
LLM_CLEANING_THRESHOLD=0.85           # higher = more LLM calls (slower but cleaner)
LLM_CLEANING_ENABLED=true             # set false to disable LLM cleaning

# Layout detection minimum region confidence:
SURYA_LAYOUT_CONFIDENCE_THRESHOLD=0.3
```

---

> [!TIP]
> Start with `HANDWRITING_FALLBACK_ENABLED=false` and `LLM_CLEANING_ENABLED=false` during initial testing. This isolates the Surya OCR quality improvement from the other layers. Add them back one at a time once Surya alone is verified working.

> [!NOTE]
> The AI extraction pipeline (Ollama/LLM for structured JSON extraction) is **completely untouched**. Only the text quality going INTO it changes — which should directly improve extraction accuracy.
