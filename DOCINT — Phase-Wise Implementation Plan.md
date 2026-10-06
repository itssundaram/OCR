# DOCINT — Phase-Wise Implementation Plan

Oct 6, 2026 · @Nikhil Chauhan

## Executive summary

The uploaded Master Prompt describes a much larger target platform (Surya + Paddle + Qwen multi-pipeline orchestration, field-level evidence/provenance, a GPU worker fleet, n8n-style orchestration UI) than what DOCINT (the `OCR_final` repo) currently runs. DOCINT today is a working single-pipeline system: FastAPI + React, Oracle DB, Redis/RQ, and ONE active extraction path chosen by `DIRECT_LLM_CALL` (vision-LLM fast path, or a Tesseract/img2table + text-LLM fallback path). This plan audits the gap and sequences the work needed to grow DOCINT toward the master prompt's target architecture without a rewrite.

Key gaps found in the Phase 0 audit (see next section for detail):

- **No real pipeline plugin architecture.** `app/pipelines/base.py` and `registry.py` define a `Pipeline`/`PipelineRegistry` contract but nothing implements or registers against it. All dispatch logic is hardcoded inside `services/extractor.py`.
- **No field-level provenance/evidence system.** Confidence and raw text exist per job, but there is no bounding-box crop, no evidence asset store, and no "click a field to see its source image" capability the frontend can call.
- **No GPU worker abstraction.** There is no worker registration, capability reporting, or health/VRAM model documented in code — Redis RQ workers run inline Python functions, not a managed worker service.
- **Config/strategy inconsistency.** `OCR_STRATEGY` defaults to `surya_with_fallback` but `ocr/orchestrator.py`'s own docstring says the system "now exclusively uses Tesseract" — the two disagree, and this must be resolved (Phase 1) before building further on top of it.
- **Dead/duplicate code.** `app/db/repositories/` is an empty, unused duplicate of the real `app/repositories/` package.
- **Two frontend UI generations coexist** (`/stitch/*` static-design screens vs. the wired wizard/dashboard flow) that are not reconciled.
- **Paddle pipeline, result comparison, evidence UI, and orchestration visualization** named in the master prompt do not exist yet in any form.

This plan keeps DOCINT's existing Qwen/Direct-LLM and Tesseract paths running throughout (per the master prompt's "do not delete, refactor" rule for the legacy pipeline) and adds the missing architecture incrementally, phase by phase, so the app stays runnable after every phase.

## Phase 0–1 — Codebase audit & target architecture

**Phase 0 — Codebase audit (status: done, this plan IS the deliverable)**

Objective: confirm what exists before changing anything.

Findings: FastAPI backend at `backend/app/`, React/Vite frontend at `frontend/src/`, Oracle DB via SQLAlchemy (`backend/app/db/models/core.py`, 12 tables matching `oracle_schema.sql`), Redis/RQ workers (`backend/app/workers/`), two extraction paths gated by `DIRECT_LLM_CALL` in `services/extractor.py`. Reusable: the repository layer (`app/repositories/*.py`), the department/template/document data model, the Ollama vision-LLM integration (`app/ai/*`), PyMuPDF rendering, img2table, the RQ queue wrapper. Dead/unused: `app/db/repositories/` (empty duplicate), `app/pipelines/*` (unused registry scaffold). Confusing: `OCR_STRATEGY` default (`surya_with_fallback`) vs. `ocr/orchestrator.py` docstring claiming Tesseract-only — needs a decision, not just documentation.

**Phase 1 — Target architecture**

Objective: define, on paper, where DOCINT is headed, so every later phase builds toward the same shape instead of improvising.

Target shape (same for RTX 5060 dev and L40S production — only configuration changes):

```
Frontend → Backend API / Orchestrator → Redis → GPU Worker(s) → Pipeline Registry (Qwen | Surya | [Paddle]) → Evidence/Result Store → Oracle DB
```

Decisions to lock in this phase:

- **Resolve the OCR\_STRATEGY conflict**: pick one real default (recommend keeping Direct-LLM/Qwen as the primary fast path per the existing README, with Surya as an explicitly-opt-in secondary pipeline — not a silent default), and fix the stale docstring in `ocr/orchestrator.py`.
- **Adopt the plugin/registry pattern for real** (`app/pipelines/base.py`/`registry.py` become the actual dispatch point, replacing the if/else branching in `services/extractor.py`).
- **GPU-agnostic by config**: no `cuda:0`, no hardcoded model paths — everything through `MODEL_ROOT`, `*_MODEL_PATH`, `CUDA` device env vars (`app/core/config.py` already centralizes most settings; extend rather than scatter).
- Delete `app/db/repositories/` (dead code) once confirmed unused.

Dependencies: none — this is a planning/decision phase, no code changes beyond deleting the dead directory and fixing the misleading docstring.

Acceptance criteria: one documented architecture diagram + config variable list that every later phase references; `OCR_STRATEGY` behavior matches its own documentation.

Risk: low. The main risk is scope creep — the master prompt describes a far larger system than DOCINT needs today; this plan deliberately treats Paddle, the n8n-style orchestration canvas, and the full GPU worker fleet as **optional, deferred** phases (flagged below) rather than mandatory, in line with the master prompt's own "do not overengineer" rule (section 62).

## Phase 2–3 — Data model & storage/asset lifecycle

**Phase 2 — Data model**

Objective: close the gap between the 12 existing Oracle tables and the fields the master prompt requires for provenance, auditability and multi-pipeline comparison.

Already present and reusable as-is: `DEPARTMENTS`, `TEMPLATES` (with version + active-template-per-code constraint), `DOCUMENTS`, `PROCESSING_JOBS`, `PROCESSING_RESULTS`. Already defined but currently unpopulated by the extractor (forward-looking schema worth keeping): `DOCUMENT_PAGES`, `ASSETS`, `PIPELINE_RUNS`, `FIELD_EXTRACTIONS`, `TABLE_EXTRACTIONS`, `PROCESSING_EVENTS`, `TEMPLATE_FIELDS`.

Work: wire the extractor to actually populate `DOCUMENT_PAGES` (one row per page, not just per document), `PIPELINE_RUNS` (one row per pipeline attempt — this is what later enables pipeline comparison), and `FIELD_EXTRACTIONS` (one row per extracted field with page/bbox/pipeline/confidence — this is the provenance record the master prompt requires in section 11). Add an Alembic migration rather than editing `core.py` models silently; DOCINT already uses Alembic (`backend/migrations/`), so this is additive, not a new tool.

Dependencies: Phase 1 decisions (pipeline registry shape) determine what `PIPELINE_RUNS.pipeline` values look like.

**Phase 3 — Storage & document asset lifecycle**

Objective: introduce the `evidence asset` abstraction (master prompt section 13/29) without which "click a field, see its crop" is impossible.

Current state: `backend/data/{documents,thumbnails,results,temp,logs}` is a flat local-filesystem layout; `app/services/asset_service.py` exists but its current scope should be audited and extended, not replaced.

Work: define asset types (original page, preprocessed page, field crop, table crop) each as a row referencing a file path, and introduce a thin `StorageService` interface over the filesystem (matching the master prompt's storage-abstraction rule in section 29) so moving from local disk to NFS/S3/MinIO later is a config change, not a rewrite. Keep the current local-filesystem backend as the only implementation for now — do not build S3 support until there's a real deployment need.

Acceptance criteria: a processed document has queryable page rows, at least one pipeline-run row, and field rows with page/bbox references — even before the frontend can display crops (that's Phase 16).

Risk: medium — touches the hot path (`services/extractor.py`) for every document processed; needs the Phase 25 test suite in place before merging, and should ship behind a feature flag so the old lighter-weight write path can be restored if write volume becomes a problem.

## Phase 4–5 — Pipeline interface/registry & configurable preprocessing

**Phase 4 — Pipeline interface & registry (highest architectural priority)**

Objective: replace the if/else dispatch buried in `services/extractor.py` with the registry pattern `app/pipelines/base.py`/`registry.py` already define but nothing uses.

Work: give `OCRPipeline` a real contract — `preprocess()`, `detect_regions()`, `extract_fields()`, `extract_tables()`, `evaluate_confidence()`, `fallback()`, `normalize()`, returning a shared `PipelineResult`/`FieldResult` shape (master prompt sections 14 and 16). Wrap the **existing** Direct-LLM/Qwen logic and the **existing** Tesseract/img2table logic as the first two registered pipelines (`QwenPipeline`, `LegacyOCRPipeline`) — this is a refactor of what already works, not new extraction logic. Each pipeline declares its capabilities (OCR / layout / vision-language / table) per section 32, so the orchestrator never special-cases a pipeline by name.

Dependencies: Phase 1's architecture decisions; Phase 2's `PIPELINE_RUNS` table to persist results against.

**Phase 5 — Configurable preprocessing**

Objective: make `app/ocr/preprocessing.py` an explicit, toggleable pipeline stage instead of implicit behavior baked into page rendering.

Work: orientation detection, deskew, grayscale/contrast/denoise, border removal, DPI normalization — each individually enable/disable-able via config, with the original image always preserved alongside the processed one (master prompt section 5). Record which operations ran and their parameters as processing metadata (feeds Phase 15's audit trail). Reuse the current preprocessing code where it already does this; add the missing per-operation toggles and the metadata record.

Acceptance criteria: a pipeline can be added by implementing the interface + registering it, with zero changes to the orchestrator; preprocessing operations can be flipped on/off per template without code changes.

Risk: Phase 4 is the riskiest phase in the whole plan — it touches the core extraction path used by every document. Ship it behind a config flag that can fall back to the current `services/extractor.py` logic, and do not remove the old path until the new one has run in parallel (shadow mode) on real documents.

## Phase 6–8 — OCR pipelines: Surya, Paddle, Qwen/Direct-LLM refactor

**Phase 8 — Qwen / Direct-LLM refactor (do this first, before Surya)**

This is explicitly the master prompt's "do not delete" pipeline (section 8), and it is DOCINT's actual production path today, so it is sequenced first even though the master prompt lists it last. Work: wrap the existing `ai/orchestrator.py` + `ai/ollama.py` + `prompt_builder.py` + `validator.py` + `confidence.py` + `response_builder.py` chain behind the Phase 4 pipeline interface, add region/field-crop awareness so it stops sending whole-page images when a template only needs a few fields (master prompt section 4's "do not OCR the whole page" rule), and keep every existing template/prompt-building behavior intact.

**Phase 6 — Surya pipeline**

Current state: `requirements.txt` already pins `surya-ocr`, `transformers`, `torch`, `timm`, and `_process_single_page` in `extractor.py` already has a Surya/TrOCR code branch gated by `OCR_STRATEGY` — so this is partially built, just not isolated or registered as a standalone pipeline. Work: extract that logic into a standalone `SuryaPipeline` conforming to the Phase 4 interface: layout/region detection → crop → Surya OCR → table regions to TATR → low-confidence field → TrOCR → still-low-confidence → VLM crop fallback (never full-page), per master prompt section 6.

**Phase 7 — Paddle pipeline (recommend: defer)**

PaddleOCR/PaddleOCR has no existing integration anywhere in this codebase — unlike Surya, this is greenfield work, not a refactor. Recommend deferring Phase 7 until Phases 4–6 and 9–11 are stable and there is a concrete document type Surya/Qwen handle poorly (e.g., dense handwriting or non-Latin scripts) that justifies the new dependency, GPU memory budget, and maintenance burden. Build it against the same pipeline interface when that need is confirmed — the architecture from Phase 4 is designed so this is additive.

Dependencies: Phase 4 (interface/registry) must exist before any of these three are "pipelines" rather than ad hoc code.

Acceptance criteria: Qwen path behaves identically to today's production behavior after refactor (regression-tested against real documents before merge); Surya becomes selectable per template/department without being the hard-coded config default; Paddle ships only when a real business need is identified.

Risk: Surya extraction carries real risk of behavior drift since it's being pulled out of an embedded branch — budget time for side-by-side comparison against the current inline behavior before switching any department over to it.

## Phase 9–11 — Confidence-based fallback, Redis job system, GPU worker service

**Phase 9 — Confidence & fallback**

Objective: make the existing confidence threshold (`app/ai/confidence.py`) configurable and field-scoped rather than document-scoped, per master prompt section 9's rule: "fallback must happen at the field/region level, not automatically at the entire document level." Work: a `CONFIDENCE_THRESHOLD` config value (not hardcoded 90%, per section 10), an automatic fast path that skips LLM calls entirely when every required field/table is found above threshold and validation passes, and a field-level retry chain (weak OCR → stronger OCR → crop-only VLM call) instead of re-running the whole document.

**Phase 10 — Redis job system hardening**

Current state: `app/workers/queue.py` already wraps a Redis connection + RQ queue with a graceful `BackgroundTasks` fallback when Redis is unreachable — this is solid groundwork. Work: add explicit job states matching master prompt section 36 (`queued, running, completed, warning, failed, skipped`), bounded retry policies that distinguish transient infra failures (retry) from deterministic model/data errors (do not retry indefinitely, per section 49), and worker registration/heartbeat so the API can tell a worker is alive versus just that Redis has a queue.

**Phase 11 — GPU worker service (recommend: scope down)**

The master prompt describes a full worker fleet with capability discovery, VRAM-aware scheduling and model-coexistence measurement (sections 23, 32, 52) — that scope fits a multi-GPU production deployment, not DOCINT's current single-box dev setup. Scoped-down work for now: wrap `run_worker.py` so it reports `worker_id`, detected GPU (name/memory via `torch.cuda.get_device_properties`, never hardcoded `cuda:0`), and which pipelines/models it has loaded, written to a small Redis-backed registry key the dashboard can read. Defer full multi-worker VRAM scheduling until there is more than one GPU worker to schedule across.

Dependencies: Phase 4 (pipelines must declare capabilities for a worker to report them); Phase 2 (`PROCESSING_EVENTS` for job-state transitions).

Acceptance criteria: a job's state transitions are visible end-to-end (queued→running→completed/failed) without reading Redis directly; the confidence threshold can be changed via `.env` without a code change and takes effect on the next job.

Risk: low for Phase 9–10 (additive to code that already works); Phase 11 risk is mostly scope risk — resist building the full fleet-management system the master prompt describes until DOCINT actually runs on more than one GPU worker.

## Phase 12–16 — Parallel processing, orchestration engine, events, result aggregation, evidence system

**Phase 12 — Parallel processing**

Current state: `services/extractor.py` already uses a `ThreadPoolExecutor` with `OCR_MAX_WORKERS` for per-page parallelism. Work: extend this to pipeline-level parallelism once Phase 4 allows more than one registered pipeline to run per page, with controlled concurrency based on measured GPU memory rather than assumed unlimited concurrency (master prompt section 19–20 — "do not assume all models can remain loaded simultaneously"). Benchmark before adding concurrency limits; don't guess them.

**Phase 13 — Orchestration engine**

Objective: a pipeline-agnostic orchestrator that drives Document → render → preprocess → detect regions → \[registered pipelines\] → field/table results → confidence → fallback → normalize → validate → compare → final result, matching master prompt section 33. This is the direct payoff of Phases 4 and 9: once pipelines share an interface and fallback is field-scoped, the orchestrator becomes a thin sequencer rather than today's embedded logic in `extractor.py`.

**Phase 14 — Real-time events**

Work: emit the event vocabulary from master prompt section 34 (`DOCUMENT_UPLOADED`, `PAGE_RENDERED`, `PIPELINE_STARTED/COMPLETED/FAILED`, `FIELD_EXTRACTED`, `FALLBACK_STARTED/COMPLETED`, `DOCUMENT_COMPLETED`, etc.) into the existing `PROCESSING_EVENTS` table (already modeled, currently unpopulated) and over a lightweight channel the frontend can subscribe to (Redis pub/sub is the natural fit given Redis is already in place — no new infrastructure needed).

**Phase 15 — Result aggregation**

Work: field-by-field comparison across pipeline runs once more than one pipeline can run per document (master prompt section 37) — store the actual decision metadata (which pipeline won, why: confidence/validation/priority) rather than inventing a rationale after the fact. Until Phase 6/7 ship a second real pipeline, this phase has nothing to compare against and should be deferred in lockstep with them.

**Phase 16 — Evidence system (API layer)**

Work: expose the Phase 3 asset store and Phase 2 `FIELD_EXTRACTIONS` rows through a `GET /documents/{id}/fields/{field_id}/evidence` style endpoint returning the exact crop, page, bbox, confidence, pipeline, model and fallback history for one field — this is what Phase 18's frontend evidence viewer calls.

Dependencies: this whole group depends on Phases 2–4 and, for Phase 15, on at least two registered pipelines existing.

Acceptance criteria: a document's processing can be reconstructed after the fact purely from stored events and field rows, with no need to read application logs.

Risk: medium — mostly integration risk (many small pieces wired together) rather than any single risky change; sequence strictly after Phases 2–4 or this becomes speculative plumbing with nothing real to carry.

## Phase 17–22 — Frontend: dashboard, OCR results UX, orchestration UI, comparison UI, template management, logging UI

**First, a reconciliation step this group depends on:** `frontend/src/App.jsx` currently routes two UI generations side by side — the `/stitch/*` static design screens and the wired `Dashboard/Upload/DocumentDetail` + wizard flow. Before building new screens, decide which generation is the real target (the wizard/dashboard flow appears to be the one actually wired to live data) and either retire or clearly separate the `/stitch/*` screens as reference mockups. Building Phase 18–22 screens on top of an unresolved split doubles the work.

**Phase 17 — Basic frontend (status: largely done)**

Dashboard, Departments, Upload, Documents, DocumentDetail already exist and are wired to real services (`frontend/src/services/*.js`). Remaining gap: a visible Logs screen and a cleaner Templates management screen (next to `CreateTypePage`/`CreateFieldsPage`).

**Phase 18 — OCR results UX**

Work: on `DocumentDetail`, add the three-pane view the master prompt names in section 39–40: extracted values (field/value/confidence/status/evidence), raw OCR output, and final payload JSON, with a click-a-field-to-see-evidence interaction calling the Phase 16 evidence endpoint.

**Phase 19–20 — Orchestration & comparison UI (recommend: defer until Phase 13/15 exist)**

The master prompt's n8n-inspired workflow canvas (section 35) is explicitly meant to be simpler than a developer workflow builder, enterprise-oriented. Build a minimal version only once there is a real orchestration engine (Phase 13) and more than one pipeline's results to compare (Phase 15) — otherwise this is a UI for data that doesn't exist yet.

**Phase 21 — Template management UI**

Work: extend the existing `CreateTypePage`/`CreateFieldsPage` wizard with versioning (master prompt section 46: a document processed under template version X must stay associated with that version), duplicate/archive actions, and region definitions once Phase 2's `TEMPLATE_FIELDS` table is populated.

**Phase 22 — Logging/observability UI**

Work: a Logs screen reading from Phase 14's event stream — processing detail, worker information, errors, timings per master prompt section 47.

Dependencies: Phase 18 needs Phase 16; Phase 19–20 need Phases 13 and 15; Phase 21 needs Phase 2; Phase 22 needs Phase 14.

Acceptance criteria: a normal user's flow (department → template → upload → process → results) never requires understanding Redis, workers, GPUs or CUDA (master prompt section 38/58) — those stay in admin/debug views only.

Risk: low for 17–18 and 21–22 (incremental UI on working data); Phase 19–20 carries schedule risk if started before its backend dependencies exist — resist building it early just because it's visually impressive.

## Phase 23–25 — Security, performance, testing

**Phase 23 — Security**

Current state: CORS is already restricted to localhost in `app/main.py`; `app/core/security.py` exists but its actual coverage needs auditing. Work: authentication/authorization and role-based access (none of DOCINT's routes currently appear to require auth — confirm this is intentional for an internal/offline deployment, per README's air-gapped framing, or add it if not), department-level isolation on API access, file-type/size validation on upload (check this exists already in `documents.py`/`extract.py`, extend if not), secret management (move any remaining inline credentials fully into `.env`), and audit logging tied into Phase 14's event stream.

**Phase 24 — Performance**

Work: benchmark, don't assume — model load time, VRAM usage, inference latency, concurrent inference, queue wait time, on the actual RTX 5060 dev box before setting any concurrency limits in Phase 12. The master prompt is explicit that "all models can remain loaded simultaneously" must not be assumed; DOCINT's offline/single-GPU deployment makes this doubly important since there's no horizontal scaling to hide a bad assumption.

**Phase 25 — Testing**

Current state: `backend/tests/{api,integration,unit}` directories exist but are effectively empty (`__init__.py` only) — this is the most significant gap for everything else in this plan. Work: unit tests for preprocessing, bounding-box handling, confidence/validation logic, and the Phase 4 pipeline interface itself (a pipeline that doesn't implement the contract correctly should fail fast in CI, not in production); integration tests against Redis/worker/DB/storage; end-to-end tests for the upload→job→pipeline→field→evidence→result flow, including partial-failure scenarios (one pipeline fails, the document still completes with a warning, per master prompt section 48).

Dependencies: Phase 25 should start in parallel with Phase 2–4, not after — every phase from here on should add to this suite as it goes, per the master prompt's own rule (section 53): "every major phase must add tests."

Acceptance criteria: CI runs a real test suite with non-trivial coverage of the pipeline interface and the extraction hot path; a documented benchmark report exists before Phase 11/12 concurrency limits are finalized.

Risk: the main risk here is sequencing — if Phase 25 is treated as a phase to do "later" rather than a parallel, ongoing practice, every refactor in Phases 4–16 ships without a safety net.

## Phase 26–27 — Deployment & production hardening, plus sequencing summary

**Phase 26 — Deployment documentation**

Work: document setup for both the RTX 5060 dev box and the L40S production target per master prompt sections 21/31/66 — NVIDIA driver, CUDA/runtime compatibility, Python/venv, PyTorch GPU build, model installation/cache, environment variables, Redis/storage connectivity, worker startup, health checks, restart procedure. DOCINT's `start.bat`/`start.sh` and `README.md` already cover local dev startup; this phase extends that into a real checklist for a second machine, without assuming CUDA version compatibility ahead of verifying it.

**Phase 27 — Production hardening**

Final review pass: security (Phase 23 follow-through), reliability, observability, backups, retries, storage retention (Phase 3's lifecycle rules enforced, not just defined), failure recovery, migrations (Alembic), deployment/rollback procedure, monitoring.

---

### Sequencing & risk summary

| Group | Phases | Depends on | Risk |
| --- | --- | --- | --- |
| Foundation | 0–1 | — | Low — planning + dead-code cleanup |
| Data & storage | 2–3 | Phase 1 | Medium — touches the hot path |
| Pipeline core | 4–5 | Phase 1–2 | **High** — refactors the production extraction path |
| OCR pipelines | 6, 8 (now), 7 (deferred) | Phase 4 | Medium (Surya), low (Qwen wrap) |
| Confidence/jobs/workers | 9–11 | Phase 4, 2 | Low–medium |
| Orchestration/events/evidence | 12–16 | Phases 2–4, 9 | Medium — integration-heavy |
| Frontend | 17–22 | Corresponding backend phase | Low, except 19–20 (defer) |
| Hardening | 23–25 | Ongoing from Phase 2 | Low if tests run in parallel throughout |
| Deployment | 26–27 | Everything above | Low — documentation + checklist |

**Recommended execution order:** 0 → 1 → 25 (start, ongoing) → 2 → 3 → 4 → 8 → 9 → 10 → 6 → 12 → 13 → 14 → 15 → 16 → 17 → 18 → 21 → 22 → 11 → 23 → 24 → 26 → 27, with Phase 7 (Paddle) and Phases 19–20 (orchestration/comparison UI) pulled in only once a concrete need and sufficient backend data exist, respectively.

**Non-negotiables carried through every phase, per the master prompt:** keep the application runnable after each phase; never hardcode `cuda:0`, RTX 5060 or L40S into application logic; never delete the Qwen/legacy pipeline, only refactor it; run tests after each phase and report what changed, what remains, and any new risk before moving on.
