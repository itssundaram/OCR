# DOCINT — Production Hardening Checklist (Phase 27)

Oct 2026. A status checklist, not a to-do list written from scratch — each
item says what's actually in place today versus what remains, checked
against the real code rather than assumed.

## Security

- [x] CORS restricted (not wide-open) — see `app/main.py`.
- [x] Upload file-type/size validation — `validate_file()`, used in
  `app/api/routes/extract.py`.
- [ ] Authentication/authorization — **explicitly out of scope for now**,
  per current product decision (internal/offline deployment). Revisit this
  checklist if that changes; nothing here assumes auth exists.
- [ ] Department-level access isolation — moot without auth; revisit
  together.
- [ ] Secrets fully out of source control — confirm `.env` (not
  `.env.example`) is gitignored on every deployment machine; `ORACLE_PASSWORD`
  and any Ollama/API keys should never be committed.
- [ ] Audit logging tied to the event stream — `PROCESSING_EVENTS` captures
  processing-level activity (Phase 14) but nothing yet logs *who* triggered
  an action, since there's no auth layer to attribute it to.

## Reliability

- [x] Job state machine with bounded retries, distinguishing transient vs.
  deterministic failures (Phase 10 — `app/services/extractor.py`'s
  `_is_transient_error`/`_rq_retries_remaining`).
- [x] A failed pipeline in a multi-pipeline comparison doesn't fail the
  whole job — `run_pipeline_comparison_job` continues with whatever
  pipelines succeeded and flags the rest (Phase 13/15).
- [ ] Worker heartbeat / liveness beyond request-time GPU/Redis checks —
  `GET /workers/health` reports live status when *queried*, but there's no
  background alerting if a worker silently dies between requests.
- [ ] Dead-letter handling for jobs that exhaust all retries — they end up
  `FAILED` with `error_message` set, but nothing automatically surfaces
  that to an operator beyond the Logs page.

## Observability

- [x] Structured event trail per job (`PROCESSING_EVENTS`), exposed via
  `GET /processing/{job_id}/events` and `GET /events/recent` (Phase 14/22).
- [x] A Logs UI (`/logs`) surfacing recent activity plus Redis/GPU/pipeline
  status.
- [ ] Metrics/alerting (Prometheus, log aggregation, etc.) — none wired up;
  the event table is queryable but not shipped anywhere external yet.
- [ ] Real-time push (WebSocket/SSE) — events are polled, not pushed (see
  `app/api/routes/events.py`'s module docstring for why this was the
  deliberate choice this session, given the environment couldn't exercise
  a live Redis pub/sub channel before the GPU pass).

## Storage & retention

- [x] Page images, thumbnails, and (if a pipeline ever sets one) field
  crops are persisted as `ASSETS` rows with file paths (Phase 3/16) — now
  also wired into the OCR/Orchestration page flows (`orchestrator.py`),
  not just the legacy extraction path.
- [ ] Retention/cleanup policy — nothing currently deletes old page images,
  thumbnails, or temp files. `TEMP_DIR` in particular should get a cleanup
  job before this runs unattended for weeks.
- [ ] Backups — no documented Oracle backup/restore procedure, and no
  backup of the `data/` asset directories.

## Testing

- [x] Unit tests for the pure-logic pieces: consensus/voting, pipeline
  registry, preprocessing config, confidence/fallback classification, job
  retry policy, GPU concurrency guard, orchestrator persistence (10 files
  under `backend/tests/unit/`).
- [ ] Integration tests against a real Redis/worker/DB — none yet; this
  environment can't run them either (no Linux-compatible Python
  environment with the project's dependencies installed in this session —
  verification here has been `py_compile`-level only).
- [ ] End-to-end tests (upload → job → pipeline → field → evidence →
  result, including partial-failure scenarios) — not written yet.
- [ ] A documented benchmark report (Phase 24) — blocked on the GPU
  configuration pass, same as the main implementation plan notes.

## Migrations & rollback

- [x] Alembic is in use (`backend/migrations/`) and additive migrations
  were used this session rather than editing models silently.
- [ ] A documented rollback procedure beyond "Alembic downgrade + redeploy
  previous build" — not written.

## Net assessment

Reliability and observability are now reasonably solid for a single-box
internal deployment: job states, retries, partial-failure handling, an
event trail, and a Logs UI all exist and were verified by direct code
reading this session. The real remaining gaps before this could be called
"production hardened" are: real integration/e2e test coverage (needs a
working Python environment to actually run pytest, which your laptop's
execution policy currently blocks), storage retention/backups, and the
GPU benchmarking pass — none of which can be faked or shortcut from here.
