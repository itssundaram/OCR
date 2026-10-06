# Department Integration Guide

Welcome to the **DocInt OCR Service**. This platform is hosted centrally, and each department can interact with it exclusively via our REST API. You do not need to use the frontend UI to extract data; you can integrate our extraction engine directly into your existing department software.

This document outlines everything a department needs to know to submit documents and retrieve extracted data automatically.

---

## 1. Prerequisites

Before integrating, you must:
1. Ensure your department (`department_slug`) and the specific document types you need to extract (`template_code`) have been configured in the DocInt system by the central administrator.
2. Ensure you have the hostname or IP address of the DocInt server (in these examples, we will use `http://docint-server.internal:8000`).

---

## 2. API Workflow

The extraction is asynchronous because AI/OCR processing takes time. The workflow is:
1. **Submit** the document (PDF, PNG, JPG).
2. **Receive** a `job_id`.
3. **Poll** the status endpoint using the `job_id` until it returns `COMPLETED`.
4. **Retrieve** your structured JSON data.

---

## 3. The Endpoints

All endpoints follow this structure:
`/api/v1/{department_slug}/{template_code}/...`

### A. Discover Endpoints
If you need to programmatically list your endpoints, use:
**GET** `/api/v1/{department_slug}/{template_code}/endpoints`

**Example cURL:**
```bash
curl -X GET "http://docint-server.internal:8000/api/v1/tms/tms_issue_unit/endpoints"
```

---

### B. Submit a Document for Extraction
Upload a document to queue it for processing.

**POST** `/api/v1/{department_slug}/{template_code}`

**Headers:**
- `Content-Type: multipart/form-data`

**Form Data:**
- `file`: The actual file binary (PDF, PNG, JPEG).

**Example cURL:**
```bash
curl -X POST "http://docint-server.internal:8000/api/v1/tms/tms_issue_unit" \
     -H "accept: application/json" \
     -H "Content-Type: multipart/form-data" \
     -F "file=@/path/to/your/document.pdf"
```

**Successful Response (202 Accepted):**
```json
{
  "success": true,
  "data": {
    "job_id": "c1f2b3e4...",
    "document_id": "d5a6b7c8...",
    "status": "QUEUED",
    "dispatched_via": "redis",
    "status_url": "http://docint-server.internal:8000/api/v1/tms/tms_issue_unit/status/c1f2b3e4..."
  },
  "error": null
}
```
*Save the `job_id` from this response!*

---

### C. Poll for Status and Results
Use the `job_id` to check if the extraction is finished. We recommend polling every 3-5 seconds.

**GET** `/api/v1/{department_slug}/{template_code}/status/{job_id}`

**Example cURL:**
```bash
curl -X GET "http://docint-server.internal:8000/api/v1/tms/tms_issue_unit/status/c1f2b3e4..."
```

**Status Response While Processing:**
```json
{
  "success": true,
  "data": {
    "job_id": "c1f2b3e4...",
    "status": "PROCESSING",
    "department": "tms",
    "template": "tms_issue_unit"
  },
  "error": null
}
```

**Status Response When Completed (Includes Results):**
When the `status` turns to `"COMPLETED"`, the response will include a `"result"` object containing the structured data.

```json
{
  "success": true,
  "data": {
    "job_id": "c1f2b3e4...",
    "status": "COMPLETED",
    "department": "tms",
    "template": "tms_issue_unit",
    "overall_confidence": 0.98,
    "result": {
      "template_version": 2,
      "fields": {
        "invoice_number": {
          "value": "INV-2026-901",
          "confidence": 0.99,
          "found": true,
          "match_strategy": "llm"
        }
      },
      "tables": [
        {
          "table_name": "items_table",
          "rows": [
            {
              "Item": "Laptop",
              "Quantity": "2",
              "Price": "$2000"
            }
          ]
        }
      ]
    }
  },
  "error": null
}
```

---

## 4. Understanding the Result JSON

When your document successfully completes, the `result` object contains exactly what your application needs to integrate into its own database.

- `fields`: A key-value pair of all standalone data points extracted.
  - `value`: The actual extracted text/number.
  - `confidence`: A score between 0.0 and 1.0. (Above 0.90 is generally highly accurate).
  - `found`: Boolean indicating if the OCR engine found the field. If false, the field was missing from the document.
- `tables`: An array of tables extracted.
  - `table_name`: The identifier for the table.
  - `rows`: An array of JSON objects, where the keys are the actual column headers from the document, and the values are the row data.

## 5. Error Handling

If a document fails to process (e.g., corrupt file, completely illegible), the status endpoint will return `"status": "FAILED"`.

```json
{
  "success": true,
  "data": {
    "job_id": "c1f2b3e4...",
    "status": "FAILED",
    "error_message": "Failed to parse PDF document. File may be corrupted."
  },
  "error": null
}
```

If you receive a `FAILED` status, manual intervention or a re-upload of a clearer document is required.
