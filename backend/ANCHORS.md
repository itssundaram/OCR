# Anchors and hardware auto-configuration (Phase 0 + Phase 1a)

## What changed for the "saved anchors are ignored" problem
1. **Verified anchors are read first.** `learned_anchor` loads the template's anchors before the pairs-first
   shortcut. For every field with a human-confirmed box (template locked, or the field saved by a user) the value
   is read from that box using the words of the single full-page OCR pass (`app/anchors/resolver.py`).
   Order per field: verified box → label search on every page → discovered pair → low-confidence reading.
2. **The box follows the page.** Boxes are stored page-relative (0–1) with the printed label next to them; if the
   label is found, the box is shifted by the same offset; a field whose own label is not found reuses the median
   shift measured from the other fields. Different render size/DPI works through the relative box.
3. **Saves no longer lose data.** `lock-anchors` and `manage-template-field` share one merge-safe versioned store
   (`app/anchors/store.py`): fields missing from a request are kept, each field stores page number, relative box,
   label, value shape; every save bumps `version` and keeps the previous version (`POST /templates/{id}/rollback-anchors`).
   The frontend Lock button now sends `job_id` so the label/value shape are learned from that job's OCR words.
4. **No second OCR.** The warm path reuses the words from the first pass. Cold-path auto-save no longer overwrites
   boxes a user saved.
5. **surya / tesseract / fast_ocr** read the verified box too (they used to only raise confidence / ignore anchors).
6. **Re-run** (`POST /extract/jobs/{id}/rerun`) processes the same stored file again with the current template and
   shows the new job; wired to the Re-run buttons.
7. Stuck jobs: `app/workers/reaper.py` fails PROCESSING jobs that outlived their timeout (runs with the API).
   `start.sh` starts `run_worker.py` (+ `run_fast_worker.py`), `--reload` only with `DOCINT_DEV=1`.

New `extraction_method` values: `anchor_verified`, `anchor_stored`, `label_search`, `anchor_low_confidence`.
Job metadata: `mode = ANCHOR_VERIFIED`, `anchor_trace` (per field: method, aligned, dx, dy, reason).

## Hardware auto-configuration
`python -m app.system.explain` (or `GET /api/v1/system/profile`) prints what was detected (GPU, free VRAM, compute
capability, driver/CUDA, PyTorch build vs GPU architecture, ONNX providers, CPU/RAM incl. container limits, disk,
Ollama/Redis/Tesseract) and every chosen setting with its reason and source.
Precedence: env override (`DOCINT_*`) > calibration (later phase) > rules > CPU defaults.
Settings you set explicitly in `.env` are never overwritten. Numbers are provisional until the calibration step.
RTX 50-series needs a PyTorch build with `sm_120` (CUDA 12.8+); the profile warns and falls back to CPU for PyTorch
models when the build cannot run on the GPU.
