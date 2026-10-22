# fast_ocr pipeline

A separate pipeline (`pipeline_mode = fast_ocr`). Existing pipelines are untouched.

## Run it
1. `git pull`, then restart the API as usual.
2. Start the dedicated worker (keep it running; it stays warm, one job at a time, no fork per job):
   `python run_fast_worker.py`
   It serves the queue `FAST_QUEUE_NAME` (default `docint-fast`). If no fast worker is listening,
   jobs fall back to the main queue. If Redis is down the API answers 503 `QUEUE_UNAVAILABLE`.
3. In the OCR page choose pipeline **fast_ocr**, or `POST /api/v1/extract/pipeline/fast_ocr`.
   The response includes `events_url`.

## What it does
- Page triage: real text layer is used directly (no OCR) unless the page is mostly an image.
- Scanned pages: one Tesseract pass per page, cached on disk (file SHA-256 + page + scale + engine version).
- Fields first: key:value scan, template matching, label-proximity fallback, optional single-crop VLM repair
  (accepted only if OCR of the same crop contains the VLM's tokens, so it cannot invent values).
- Field states: `validated`, `review_required`, `failed`, `extracted` (discovered pairs).
- Tables from ruled-line geometry (time-boxed by `FAST_TABLE_TIMEOUT_S`; never blocks fields).
- Paragraph / heading / list blocks in reading order.

## Live events
`GET /api/v1/stream/jobs/{job_id}` (SSE). Envelope: `{v, job_id, seq, ts, type, page, data}`.
Reconnect with `Last-Event-ID`; `resync_required` means reload the job result instead.
`POST /api/v1/stream/jobs/{job_id}/cancel` stops the job between steps.
`field_validated` is only sent after the value is saved to the database.

## Known limits
- Quality on poor scans is bounded by Tesseract; low-confidence values are flagged `review_required`.
- Tables without ruled lines depend on word-gap column detection; TATR fallback is off by default.
- Pages whose text is rotated 90/270 degrees keep their text in the right place but are not regrouped into lines.
- Timings measured so far were on a 2-core VM, not your GPU server; benchmark there.
