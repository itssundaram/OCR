# How departments use DOCINT through the API

This is document 2 of 3. A department system does not need the web UI: it sends a file over HTTP, polls for the result, and reads structured JSON.

Base address used below: `http://docint-server:8000` (replace with the server name or IP). Every path starts with `/api/v1`. Interactive reference of every endpoint: `http://docint-server:8000/docs` (shown unless `APP_ENV=production`).

---

## 1. One-time preparation (done by the DOCINT administrator, in the UI or with the calls in section 7)

1. The **department** exists (`TMS`, `MMS`, `PSG`, `CUE_TMS`, `CUE_PSG`, `CUE_MMS` are created by the database script). Department names in URLs are case-insensitive.
2. A **template** exists for each document type: a code (for example `backload`) and its field list.
3. That template version is **activated**. Uploads against an inactive or unknown template are refused with `TEMPLATE_NOT_FOUND`. Activating a new version of the same code switches the department to it.

The department then needs two things only: the server address, and `department/template` for each document type, for example `tms/backload`.

---

## 2. The flow

```
POST  /api/v1/{department}/{template}/pipeline/fast_ocr     send the file        -> 202 + job_id
GET   /api/v1/{department}/{template}/status/{job_id}       poll every 2-3 s     -> status, then the result
```

Processing is asynchronous: a page takes seconds, a long document more. Keep polling until `status` is one of the final states.

**Use `/pipeline/fast_ocr`.** It reads digital PDFs from their text layer, scanned pages with Tesseract, tables from page geometry, and does not depend on the vision model being fast. The plain `POST /{department}/{template}` (no pipeline in the path) runs the older flow that sends page images to the Ollama vision model; on a machine without a GPU it can take minutes per page and fail with an Ollama timeout.

### Send a file

`multipart/form-data`, one part named `file`. Accepted: PDF, JPEG, PNG, TIFF, WebP, BMP, up to `MAX_UPLOAD_SIZE_MB` (50 by default). The type is checked from the file's first bytes, not only its extension.

curl on Linux / macOS:

```bash
curl -s -X POST "http://docint-server:8000/api/v1/tms/backload/pipeline/fast_ocr" \
     -F "file=@/data/scans/backload_0417.pdf"
```

curl on Windows (`curl.exe`, not the PowerShell alias):

```
curl.exe -s -X POST "http://docint-server:8000/api/v1/tms/backload/pipeline/fast_ocr" -F "file=@C:\scans\backload_0417.pdf"
```

Response, HTTP 202:

```json
{
  "success": true,
  "data": {
    "job_id": "5c1e3b0e-6f0c-4c5e-9a53-0d0e4d7a9b21",
    "document_id": "a2f4c8d1-...",
    "status": "QUEUED",
    "pipeline": "fast_ocr",
    "dispatched_via": "redis",
    "prefetch": false,
    "status_url": "/api/v1/tms/backload/status/5c1e3b0e-6f0c-4c5e-9a53-0d0e4d7a9b21",
    "events_url": "/api/v1/stream/jobs/5c1e3b0e-6f0c-4c5e-9a53-0d0e4d7a9b21"
  }
}
```

Keep `job_id`. `status_url` is relative to the server address.

### Poll for the result

```bash
curl -s "http://docint-server:8000/api/v1/tms/backload/status/5c1e3b0e-6f0c-4c5e-9a53-0d0e4d7a9b21"
```

`data.status` is one of:

| Status | Meaning | Keep polling? |
|---|---|---|
| `QUEUED`, `PROCESSING` (legacy flow also `OCR_PROCESSING`, `AI_PROCESSING`, `VALIDATING`) | not finished | yes |
| `COMPLETED` | finished, every field passed its checks | no, read `result` |
| `WARNING` | finished, but some fields are flagged for human review (low confidence or failed a check). **The result is present: use it.** | no |
| `FAILED` | could not be processed; `error_message` says why | no |
| `CANCELLED` | stopped by a person from the UI | no |

Other fields in `data`: `job_id`, `document_id`, `department`, `template`, `started_at`, `completed_at`, `overall_confidence`, `error_message`, `ocr_engine`.

---

## 3. The result

When the status is `COMPLETED` or `WARNING`, `data` also holds the result twice: `export` (stable keys, **meant for your system**) and `result` (everything the review screen uses: confidence, boxes, checks).

### `data.export` (bind your system to this)

```json
{
  "schema_version": "1.0",
  "job_id": "5c1e3b0e-...",
  "department": "TMS",
  "template": "backload",
  "template_version": 3,
  "document": { "pages": 2 },
  "fields": {
    "unit_from": "2 KUMAON",
    "iv_no": "20/4/MT/02",
    "date": "17-04-2026"
  },
  "additional_fields": { "stn": "c/o 56 APO" },
  "tables": [
    {
      "key": "table_1", "name": "Table_1", "page": 1, "row_count": 2,
      "columns": [ { "index": 0, "key": "sno", "label": "S No" }, { "index": 1, "key": "pers_no", "label": "Pers No" } ],
      "rows": [ { "sno": "1", "pers_no": "JC-12345A" }, { "sno": "2", "pers_no": "JC-12346K" } ]
    }
  ]
}
```

(The values above are illustrative; the shape is what the server returns.)

- `fields`: the template's own fields, one flat key per field name, value as text. A field that could not be read is normally an empty string `""` rather than absent, but code defensively (`fields.get(name, "")`).
- `additional_fields`: other `Label: value` pairs found on the document that are not in the template. Optional to use.
- `tables`: every table found. Each row is one object keyed by `columns[].key`. Column keys stay the same when a person later corrects a cell; they change only if the printed header text itself changes. Recognised tables may also carry `records` (columns split into normalised parts and dates as `dd-mm-yyyy`).

Fetch the same JSON later, or with per-field confidence, without polling the status endpoint:

```bash
curl -s "http://docint-server:8000/api/v1/extract/jobs/5c1e3b0e-.../export"
curl -s "http://docint-server:8000/api/v1/extract/jobs/5c1e3b0e-.../export?detail=true"
```

`detail=true` adds `field_details` per field: `confidence` (0 to 1), `page`, `verified` (a person confirmed or edited it), `state`. The export is always built from the stored result, so corrections made in the UI after processing are included.

### `data.result` (full detail)

```json
{
  "template_version": 3,
  "fields": {
    "iv_no": { "value": "20/4/MT/02", "confidence": 0.93, "page_number": 1, "bbox": { "x1": 412, "y1": 188, "x2": 560, "y2": 214 }, "state": "validated" }
  },
  "tables": [ ... ]
}
```

Use `confidence` and `state` if you want your own acceptance rule (for example auto-accept at 0.90 and above, queue the rest for a clerk). Field states: `validated`, `review_required`, `failed`, `extracted` (found on the page but not in the template).

---

## 4. Python example

```python
import time
import requests

BASE = "http://docint-server:8000/api/v1"

def extract(department: str, template: str, path: str, timeout_s: int = 300) -> dict:
    with open(path, "rb") as fh:
        r = requests.post(f"{BASE}/{department}/{template}/pipeline/fast_ocr", files={"file": fh}, timeout=60)
    r.raise_for_status()
    job_id = r.json()["data"]["job_id"]

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        s = requests.get(f"{BASE}/{department}/{template}/status/{job_id}", timeout=30)
        s.raise_for_status()
        data = s.json()["data"]
        if data["status"] in ("COMPLETED", "WARNING"):
            return data["export"]
        if data["status"] in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"{data['status']}: {data.get('error_message')}")
        time.sleep(2)
    raise TimeoutError(f"job {job_id} did not finish in {timeout_s}s")

doc = extract("tms", "backload", r"C:\scans\backload_0417.pdf")
print(doc["fields"]["iv_no"], len(doc["tables"]))
```

---

## 5. Errors

Errors always use this shape, with the HTTP status shown:

```json
{ "success": false, "error": { "code": "TEMPLATE_NOT_FOUND", "message": "No active template found for tms/backload.", "details": null } }
```

| HTTP | `error.code` | What to do |
|---|---|---|
| 404 | `DEPT_NOT_FOUND` | wrong department name |
| 404 | `TEMPLATE_NOT_FOUND` | wrong template code, or the template is not activated: ask the administrator |
| 404 | `JOB_NOT_FOUND` | wrong `job_id` |
| 404 | `PIPELINE_NOT_FOUND` | wrong pipeline name in the path (valid: `fast_ocr`, `learned_anchor`, `tesseract`, `surya`) |
| 400 | `FILE_TYPE_INVALID` | not a PDF/image, or the contents do not match the extension |
| 413 | `FILE_TOO_LARGE` | file above `MAX_UPLOAD_SIZE_MB` |
| 409 | `RESULT_NOT_READY` | `export` asked for before the job produced a result: poll status first |
| 503 | `QUEUE_UNAVAILABLE` | Redis is down; retry later (alert the administrator) |
| 503 | `DATABASE_CONNECTION_ERROR`, `AI_ENGINE_UNAVAILABLE`, `OCR_ENGINE_UNAVAILABLE` | a backend service is down; retry later |
| 422 | validation errors | malformed request (for example a missing `file` part) |
| 500 | `INTERNAL_ERROR` | server fault; the message says what failed, and the log has the stack trace |

A job that ends `FAILED` is not an HTTP error: the status call still answers 200 with `status: "FAILED"` and `error_message`. Re-upload, or press Re-run in the UI.

Retries: it is safe to retry a POST that failed with 5xx or a network error, but if it had in fact been accepted you will get two jobs for one file. Store the `job_id` as soon as you receive it.

---

## 6. Other calls departments may want

| Call | Purpose |
|---|---|
| `GET /api/v1/health` | is the system up (Oracle, Redis, workers, Ollama) |
| `GET /api/v1/{department}/{template}/endpoints` | the URLs for one department/template |
| `GET /api/v1/stream/jobs/{job_id}` | live events for a `fast_ocr` job (Server-Sent Events: each field as it is read). Reconnect with the `Last-Event-ID` header. Not needed for plain polling. |
| `POST /api/v1/stream/jobs/{job_id}/cancel` | stop a running `fast_ocr` job |
| `POST /api/v1/extract/jobs/{job_id}/rerun` | process the stored file again with the current template |
| `POST /api/v1/{department}/{template}/compare?pipelines=fast_ocr&pipelines=learned_anchor` | run several pipelines on one file and reconcile the values (same `file` part; poll the same status URL) |
| `GET /api/v1/documents/{document_id}/file` | download the stored original |
| `GET /api/v1/processing/{job_id}/events` | the job's timeline (what each step did and how long it took) |

---

## 7. Administrator calls: create and activate a template

```bash
# create (always created inactive, version number is assigned by the server)
curl -s -X POST "http://docint-server:8000/api/v1/templates/tms" \
     -H "Content-Type: application/json" \
     -d '{
           "code": "backload",
           "extraction_instructions": "Army backload voucher",
           "template_json": { "fields": [
               { "name": "unit_from", "type": "text", "description": "Unit sending the stores", "aliases": ["Unit From", "From"] },
               { "name": "iv_no",     "type": "text", "description": "IV number", "required": true },
               { "name": "date",      "type": "date", "description": "Voucher date" }
           ] }
         }'

# note "data.id" in the answer, then activate it
curl -s -X POST "http://docint-server:8000/api/v1/templates/42/activate"
```

Field `type` is `text`, `date`, `number`, `boolean` or `table`. `aliases` are other labels the printed form uses for the same field; they improve matching. Other calls: `GET /api/v1/templates` (all), `GET /api/v1/templates/{department}/{code}`, `PUT /api/v1/templates/{id}`, `POST /api/v1/templates/{id}/deactivate`, `GET /api/v1/departments`.

The same can be done in the web UI under Manage. `scripts/api_smoke_test.py --docs <folder of sample PDFs>` creates templates for the sample forms, uploads every file through this API and prints a summary; it is a working example of everything above.

---

## 8. Things to know before integrating

- **No login.** Anyone who can reach port 8000 can upload and read results. Put DOCINT on a private network and restrict access with the firewall.
- **Server-to-server calls only, by default.** Browsers on other origins are blocked by CORS (only `http://localhost:5173` and `http://localhost:3000` are allowed in `app/main.py`). A department web page that calls the API directly from the browser needs its origin added there; calls from a department's own server need nothing.
- **Accuracy is bounded by the scan.** Treat `WARNING` and low `confidence` as "needs a person". On the sample forms tested, most fields were right but some handwritten or faint values were not; do not auto-accept values you cannot afford to have wrong without a confidence threshold of your own.
- **Re-processing the same file is cheap** (OCR words are cached by file content), so a re-run after editing the template is fast.
