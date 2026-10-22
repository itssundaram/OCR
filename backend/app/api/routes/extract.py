"""
DOCINT — Department-based Extraction API
Primary extraction endpoints following the pattern: /api/v1/extract/{department_slug}/{template_code}

Three upload flows live here, matching the product's three entry points:
  - POST /{dept}/{template}                    : today's existing production
    flow (DIRECT_LLM_CALL/OCR_STRATEGY-gated, unchanged).
  - POST /{dept}/{template}/pipeline/{name}     : OCR page (Phase 13) — pick
    ONE registered pipeline; only it processes the document.
  - POST /{dept}/{template}/compare             : Orchestration page (Phase 13) —
    pick several pipelines; all run concurrently and are reconciled by
    app/ai/consensus.py.
"""
from __future__ import annotations

import base64
import io
import os
import shutil
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from PIL import Image, ImageOps
import pytesseract
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.exceptions import DocIntError
from app.core.logging import get_logger
from app.core.security import (
    generate_stored_filename,
    safe_file_path,
    sanitize_original_filename,
    validate_file,
)
from app.db.database import get_db
from app.db.models import (
    Asset,
    Department,
    Document,
    DocumentPage,
    FieldExtraction,
    ProcessingJob,
    ProcessingResult,
    Template,
)
from app.ocr.image_extractor import extract_pages_as_images
from app.pipelines.registry import get_pipeline, list_pipelines
from app.schemas.common import APIResponse
from app.services.extractor import process_document_job
from app.services.orchestrator import run_pipeline_comparison_job, run_single_pipeline_job

logger = get_logger(__name__)

router = APIRouter(tags=["Extraction"])


class ReOcrFieldRequest(BaseModel):
    job_id: str
    field_key: str
    bbox: dict[str, Any]
    page_number: int = 1


class UpdateFieldValueRequest(BaseModel):
    job_id: str
    field_key: str
    value: str
    bbox: dict[str, Any] | None = None


def _load_job_page_image(db: Session, job: ProcessingJob, page_number: int = 1) -> Image.Image:
    """Helper to locate or extract full PIL Image for a given job's page."""
    page_stmt = select(DocumentPage).where(
        DocumentPage.document_id == job.document_id,
        DocumentPage.page_number == page_number,
    )
    dp = db.execute(page_stmt).scalars().first()
    if dp and dp.image_asset_id:
        asset = db.get(Asset, dp.image_asset_id)
        if asset and os.path.exists(asset.file_path):
            img = Image.open(asset.file_path)
            img.load()
            return img.convert("RGB")

    doc = db.get(Document, job.document_id)
    if not doc or not os.path.exists(doc.file_path):
        raise DocIntError("Underlying document file could not be found.", "DOCUMENT_FILE_MISSING")

    images = extract_pages_as_images(doc.file_path, doc.mime_type)
    p_idx = max(0, page_number - 1)
    if p_idx < len(images):
        return images[p_idx].convert("RGB")

    raise DocIntError(f"Page {page_number} not available in document.", "PAGE_NOT_FOUND")


@router.post("/extract/re-ocr-field", response_model=APIResponse[dict])
def re_ocr_field(req: ReOcrFieldRequest, db: Session = Depends(get_db)):
    """
    Sub-50ms Micro-OCR on a user-corrected bounding box.
    Crops the page image, executes fast enhanced OCR, updates job results in DB,
    and returns the new value, confidence, and preview crop image.
    """
    job = db.get(ProcessingJob, req.job_id)
    if not job:
        raise DocIntError(f"Processing job {req.job_id} not found.", "JOB_NOT_FOUND")

    full_img = _load_job_page_image(db, job, req.page_number)

    x1 = max(0, int(round(float(req.bbox.get("x1", 0)))))
    y1 = max(0, int(round(float(req.bbox.get("y1", 0)))))
    x2 = min(full_img.width, int(round(float(req.bbox.get("x2", full_img.width)))))
    y2 = min(full_img.height, int(round(float(req.bbox.get("y2", full_img.height)))))

    if x2 <= x1 or y2 <= y1:
        raise DocIntError("Bounding box coordinates invalid or too small.", "INVALID_BBOX")

    crop = full_img.crop((x1, y1, x2, y2))

    gray = crop.convert("L")
    enhanced = ImageOps.autocontrast(gray)
    if enhanced.height < 40 or enhanced.width < 60:
        enhanced = enhanced.resize((enhanced.width * 2, enhanced.height * 2), Image.Resampling.LANCZOS)

    try:
        data = pytesseract.image_to_data(enhanced, output_type=pytesseract.Output.DICT, config="--psm 6")
        words = []
        confs = []
        for idx in range(len(data.get("text", []))):
            w = data["text"][idx].strip()
            c = float(data["conf"][idx]) if float(data["conf"][idx]) > 0 else 0.0
            if w:
                words.append(w)
                if c > 0:
                    confs.append(c)
        extracted_text = " ".join(words).strip()
        avg_conf = (sum(confs) / len(confs) / 100.0) if confs else (0.85 if extracted_text else 0.0)
    except Exception as e:
        logger.warning("re_ocr_pytesseract_failed", error=str(e))
        extracted_text = ""
        avg_conf = 0.0

    tight_bbox = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

    # 1. Update ProcessingResult
    res_stmt = select(ProcessingResult).where(ProcessingResult.job_id == req.job_id)
    result = db.execute(res_stmt).scalars().first()
    if result:
        ej = dict(result.extracted_json or {})
        fields = dict(ej.get("fields", {}))
        field_entry = dict(fields.get(req.field_key, {}))
        field_entry["value"] = extracted_text
        field_entry["confidence"] = round(avg_conf, 3)
        field_entry["found"] = bool(extracted_text)
        field_entry["bbox"] = tight_bbox
        field_entry["match_strategy"] = "MANUAL_RE_OCR"
        field_entry["is_verified"] = True
        fields[req.field_key] = field_entry
        ej["fields"] = fields
        result.extracted_json = ej
        flag_modified(result, "extracted_json")

    # 2. Update FieldExtraction row
    fe_stmt = select(FieldExtraction).where(
        FieldExtraction.job_id == req.job_id,
        FieldExtraction.field_name == req.field_key,
    )
    fe = db.execute(fe_stmt).scalars().first()
    if fe:
        fe.field_value = extracted_text
        fe.confidence = avg_conf
        fe.bbox_json = tight_bbox
        fe.extraction_method = "manual_re_ocr"
    else:
        new_fe = FieldExtraction(
            job_id=req.job_id,
            field_name=req.field_key,
            field_value=extracted_text,
            confidence=avg_conf,
            bbox_json=tight_bbox,
            extraction_method="manual_re_ocr",
            is_final=1,
        )
        db.add(new_fe)

    db.commit()

    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    b64_crop = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"

    return APIResponse(data={
        "field_key": req.field_key,
        "value": extracted_text,
        "confidence": round(avg_conf, 3),
        "bbox": tight_bbox,
        "image_data": b64_crop,
    })


@router.post("/extract/update-field", response_model=APIResponse[dict])
def update_field_value(req: UpdateFieldValueRequest, db: Session = Depends(get_db)):
    """
    Save manually edited text and/or bounding box for a field.
    Sets confidence to 100% (human verified ground truth).
    """
    job = db.get(ProcessingJob, req.job_id)
    if not job:
        raise DocIntError(f"Processing job {req.job_id} not found.", "JOB_NOT_FOUND")

    res_stmt = select(ProcessingResult).where(ProcessingResult.job_id == req.job_id)
    result = db.execute(res_stmt).scalars().first()
    if result:
        ej = dict(result.extracted_json or {})
        fields = dict(ej.get("fields", {}))
        field_entry = dict(fields.get(req.field_key, {}))
        field_entry["value"] = req.value
        field_entry["confidence"] = 1.0
        field_entry["found"] = True
        field_entry["manually_edited"] = True
        field_entry["is_verified"] = True
        if req.bbox:
            field_entry["bbox"] = req.bbox
        fields[req.field_key] = field_entry
        ej["fields"] = fields
        result.extracted_json = ej
        flag_modified(result, "extracted_json")

    fe_stmt = select(FieldExtraction).where(
        FieldExtraction.job_id == req.job_id,
        FieldExtraction.field_name == req.field_key,
    )
    fe = db.execute(fe_stmt).scalars().first()
    if fe:
        fe.field_value = req.value
        fe.confidence = 1.0
        if req.bbox:
            fe.bbox_json = req.bbox
    else:
        new_fe = FieldExtraction(
            job_id=req.job_id,
            field_name=req.field_key,
            field_value=req.value,
            confidence=1.0,
            bbox_json=req.bbox or {},
            extraction_method="manual_edit",
            is_final=1,
        )
        db.add(new_fe)

    db.commit()
    return APIResponse(data={
        "field_key": req.field_key,
        "value": req.value,
        "confidence": 1.0,
        "is_verified": True,
        "bbox": req.bbox,
    })


class ManageTemplateFieldsRequest(BaseModel):
    job_id: str
    action: str  # "add", "remove", "update"
    field_key: str
    value: str | None = None
    bbox: dict[str, Any] | None = None
    page_number: int = 1
    save_to_template: bool = True


@router.post("/extract/manage-template-field", response_model=APIResponse[dict])
def manage_template_field(req: ManageTemplateFieldsRequest, db: Session = Depends(get_db)):
    """
    Add, remove, or update a field in the current job result, and optionally persist
    it directly to the template's learned_anchors so future runs automatically extract it.
    """
    job = db.get(ProcessingJob, req.job_id)
    if not job:
        raise DocIntError(f"Processing job {req.job_id} not found.", "JOB_NOT_FOUND")

    template = db.get(Template, job.template_id) if job.template_id else None

    # 1. Update ProcessingResult.extracted_json
    res_stmt = select(ProcessingResult).where(ProcessingResult.job_id == req.job_id)
    result = db.execute(res_stmt).scalars().first()
    if result:
        ej = dict(result.extracted_json or {})
        fields = dict(ej.get("fields", {}))

        if req.action == "remove":
            if req.field_key in fields:
                del fields[req.field_key]
        else:  # "add" or "update"
            fields[req.field_key] = {
                "value": req.value or "",
                "confidence": 1.0,
                "found": bool(req.value),
                "bbox": req.bbox,
                "source_page": req.page_number,
                "manually_managed": True,
            }

        ej["fields"] = fields
        result.extracted_json = ej
        flag_modified(result, "extracted_json")

    # 2. Update FieldExtraction DB table
    if req.action == "remove":
        db.query(FieldExtraction).filter_by(job_id=req.job_id, field_name=req.field_key).delete()
    else:
        fe = db.query(FieldExtraction).filter_by(job_id=req.job_id, field_name=req.field_key).first()
        if fe:
            fe.field_value = req.value or ""
            fe.confidence = 1.0
            if req.bbox:
                fe.bbox_json = req.bbox
        else:
            db.add(FieldExtraction(
                job_id=req.job_id,
                field_name=req.field_key,
                field_value=req.value or "",
                confidence=1.0,
                bbox_json=req.bbox or {},
                extraction_method="user_defined",
                is_final=1,
            ))

    # 3. Persist to Template.template_json (learned_anchors)
    if req.save_to_template and template:
        tj = dict(template.template_json or {})
        la = dict(tj.get("learned_anchors", {}))
        la_fields = dict(la.get("fields", {}))

        if req.action == "remove":
            if req.field_key in la_fields:
                del la_fields[req.field_key]
        else:
            la_fields[req.field_key] = {
                "bbox": req.bbox,
                "page_number": req.page_number,
                "value": req.value,
            }

        la["fields"] = la_fields
        tj["learned_anchors"] = la
        template.template_json = tj
        flag_modified(template, "template_json")

    db.commit()

    return APIResponse(data={
        "status": "success",
        "action": req.action,
        "field_key": req.field_key,
        "value": req.value,
        "bbox": req.bbox,
        "template_updated": bool(req.save_to_template and template),
    })



def _resolve_department_and_template(db: Session, department_slug: str, template_code: str) -> Template:
    dept_stmt = select(Department).where(Department.slug == department_slug.upper())
    dept = db.execute(dept_stmt).scalars().first()
    if not dept:
        raise DocIntError(f"Department '{department_slug}' not found.", "DEPT_NOT_FOUND")

    tmpl_stmt = select(Template).where(
        Template.department_id == dept.id,
        Template.code == template_code.lower(),
        Template.is_active == 1,
    )
    template = db.execute(tmpl_stmt).scalars().first()
    if not template:
        raise DocIntError(
            f"No active template found for {department_slug}/{template_code}.",
            "TEMPLATE_NOT_FOUND",
        )
    return template


def _save_uploaded_file_and_create_job(db: Session, file: UploadFile, template: Template, pipeline_mode: str = "single_pipeline") -> tuple[str, str]:
    """Shared upload mechanics for all three flows above: validate, store the
    file, create the Document + ProcessingJob rows. Returns (doc_id, job_id);
    caller commits and dispatches."""
    header_bytes = file.file.read(2048)
    file.file.seek(0)
    file_size = getattr(file, "size", 0)
    if not file_size:
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)

    sanitized_filename = sanitize_original_filename(file.filename or "unnamed")
    mime_type = validate_file(header_bytes, sanitized_filename, file_size)

    stored_filename = generate_stored_filename(mime_type)
    file_path = safe_file_path(settings.storage_path, stored_filename)
    try:
        with open(file_path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)
    except Exception as e:
        raise DocIntError(f"Failed to save file: {str(e)}", "FILE_STORAGE_ERROR")

    doc_id = str(uuid.uuid4())
    new_doc = Document(
        id=doc_id,
        original_filename=sanitized_filename,
        stored_filename=stored_filename,
        file_path=str(file_path),
        file_type=mime_type,
        file_size_bytes=file_size,
        template_id=template.id,
        status="QUEUED",
    )
    db.add(new_doc)

    job_id = str(uuid.uuid4())
    new_job = ProcessingJob(
        id=job_id,
        document_id=doc_id,
        template_id=template.id,
        template_version=template.version,
        status="QUEUED",
        pipeline_mode=pipeline_mode,
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_doc)
    return doc_id, job_id


def _dispatch_job(
    task_path: str,
    job_id: str,
    task_args: tuple,
    background_tasks: BackgroundTasks,
    fallback_fn,
) -> str:
    """Enqueue `task_path` (a dotted import string, RQ-style) to Redis with
    Phase 10's bounded retry + on_failure policy, falling back to
    BackgroundTasks if Redis is unavailable or the installed rq version
    rejects the retry/callback kwargs. Returns "redis" or "background_task" —
    same contract the original single-flow dispatch block had, now shared by
    all three upload flows."""
    try:
        from app.workers.queue import q as redis_queue
        if redis_queue is not None:
            try:
                from rq import Retry, Callback
                redis_queue.enqueue(
                    task_path,
                    job_id,
                    *task_args,
                    job_id=job_id,  # Sets the Redis Job ID to match our DB job_id
                    job_timeout=settings.JOB_TIMEOUT_SECONDS,
                    retry=Retry(max=settings.JOB_MAX_RETRIES, interval=settings.JOB_RETRY_INTERVAL_SECONDS),
                    on_failure=Callback("app.workers.tasks.job_failed_callback"),
                )
            except TypeError:
                logger.warning("rq_phase10_kwargs_unsupported_falling_back")
                redis_queue.enqueue(
                    task_path, job_id, *task_args,
                    job_id=job_id, job_timeout=settings.JOB_TIMEOUT_SECONDS,
                )
        else:
            raise RuntimeError("Redis queue not initialised")
        return "redis"
    except Exception as redis_err:
        logger.warning("redis_dispatch_failed_fallback", error=str(redis_err), fallback="background_tasks")
        background_tasks.add_task(fallback_fn, job_id, *task_args)
        return "background_task"


@router.post("/{department_slug}/{template_code}", status_code=202)
def extract_document(
    department_slug: str,
    template_code: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload a document for extraction under the given department and template.
    Returns a job_id you can poll at the status endpoint. Unchanged from
    before Phase 13: this is still DOCINT's existing production flow
    (DIRECT_LLM_CALL/OCR_STRATEGY-gated).
    """
    template = _resolve_department_and_template(db, department_slug, template_code)
    doc_id, job_id = _save_uploaded_file_and_create_job(db, file, template)

    dispatched_via = _dispatch_job(
        "app.workers.tasks.process_document_job", job_id, (), background_tasks, process_document_job,
    )

    base_url = f"/api/v1/{department_slug.lower()}/{template_code.lower()}"
    return APIResponse(data={
        "job_id": job_id,
        "document_id": doc_id,
        "status": "QUEUED",
        "dispatched_via": dispatched_via,
        "status_url": f"{base_url}/status/{job_id}",
    })


@router.post("/{department_slug}/{template_code}/pipeline/{pipeline_name}", status_code=202)
def extract_document_with_pipeline(
    department_slug: str,
    template_code: str,
    pipeline_name: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    force_relearn: bool = Form(False),
    db: Session = Depends(get_db),
):
    """
    OCR page (Phase 13): upload with ONE explicitly chosen pipeline. Only
    that pipeline processes the document — see
    app/services/orchestrator.py::run_single_pipeline_job.

    force_relearn: set from the upload page's "this is a new format" checkbox.
    Pipelines that keep a learned geometric pattern per template (currently
    only learned_anchor) use it to skip their stored pattern and relearn from
    this document instead of trusting possibly-stale/mismatched geometry.
    Pipelines that don't have such a concept simply ignore it.
    """
    if pipeline_name not in list_pipelines():
        raise DocIntError(
            f"Unknown pipeline '{pipeline_name}'. Available: {', '.join(list_pipelines())}",
            "PIPELINE_NOT_FOUND",
        )

    template = _resolve_department_and_template(db, department_slug, template_code)
    doc_id, job_id = _save_uploaded_file_and_create_job(db, file, template, pipeline_mode=pipeline_name)

    dispatched_via = _dispatch_job(
        "app.workers.tasks.run_single_pipeline_job", job_id, (pipeline_name, force_relearn),
        background_tasks, run_single_pipeline_job,
    )

    base_url = f"/api/v1/{department_slug.lower()}/{template_code.lower()}"
    return APIResponse(data={
        "job_id": job_id,
        "document_id": doc_id,
        "status": "QUEUED",
        "pipeline": pipeline_name,
        "dispatched_via": dispatched_via,
        "status_url": f"{base_url}/status/{job_id}",
    })


@router.post("/{department_slug}/{template_code}/compare", status_code=202)
def extract_document_with_comparison(
    department_slug: str,
    template_code: str,
    background_tasks: BackgroundTasks,
    pipelines: list[str] = Query(..., description="Pipeline names to run and compare, e.g. ?pipelines=surya&pipelines=tesseract"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Orchestration page (Phase 13): upload with SEVERAL pipelines selected
    together. All of them run on the same document (concurrently) and their
    field values are reconciled — see
    app/services/orchestrator.py::run_pipeline_comparison_job and
    app/ai/consensus.py.
    """
    available = set(list_pipelines())
    unknown = [p for p in pipelines if p not in available]
    if unknown:
        raise DocIntError(
            f"Unknown pipeline(s): {', '.join(unknown)}. Available: {', '.join(sorted(available))}",
            "PIPELINE_NOT_FOUND",
        )
    if len(pipelines) < 2:
        raise DocIntError("Select at least two pipelines to compare.", "INSUFFICIENT_PIPELINES")

    template = _resolve_department_and_template(db, department_slug, template_code)
    doc_id, job_id = _save_uploaded_file_and_create_job(db, file, template, pipeline_mode="orchestration")

    dispatched_via = _dispatch_job(
        "app.workers.tasks.run_pipeline_comparison_job", job_id, (pipelines,),
        background_tasks, run_pipeline_comparison_job,
    )

    base_url = f"/api/v1/{department_slug.lower()}/{template_code.lower()}"
    return APIResponse(data={
        "job_id": job_id,
        "document_id": doc_id,
        "status": "QUEUED",
        "pipelines": pipelines,
        "dispatched_via": dispatched_via,
        "status_url": f"{base_url}/status/{job_id}",
    })


@router.get("/{department_slug}/{template_code}/status/{job_id}")
def get_extraction_status(
    department_slug: str,
    template_code: str,
    job_id: str,
    db: Session = Depends(get_db),
):
    """
    Poll the status of an extraction job.
    When COMPLETED, returns the full structured extraction result.
    """
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise DocIntError(f"Job {job_id} not found.", "JOB_NOT_FOUND")

    response_data: dict = {
        "job_id": job.id,
        "document_id": job.document_id,
        "template_id": job.template_id,
        "status": job.status,
        "department": department_slug,
        "template": template_code,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "error_message": job.error_message,
        "overall_confidence": job.overall_confidence,
        "ocr_engine": job.ocr_engine,
        "ai_engine": job.ai_engine,
    }

    # Phase 10 introduced "WARNING" as a real completion state (a document
    # that finished but has fields needing review) — it must still surface
    # its result, not be treated like a still-processing job.
    if job.status in ("COMPLETED", "WARNING"):
        if job.document_id:
            stmt_pages = select(DocumentPage).where(DocumentPage.document_id == job.document_id).order_by(DocumentPage.page_number)
            pages = db.execute(stmt_pages).scalars().all()
            if pages:
                response_data["doc_page_width"] = pages[0].width
                response_data["doc_page_height"] = pages[0].height

        result_stmt = select(ProcessingResult).where(ProcessingResult.job_id == job_id)
        result = db.execute(result_stmt).scalars().first()
        if result:
            response_data["result"] = result.extracted_json
            response_data["ocr_metadata_json"] = result.ocr_metadata_json
            response_data["confidence_json"] = result.confidence_json

    return APIResponse(data=response_data)


@router.get("/{department_slug}/{template_code}/endpoints")
def get_department_endpoints(
    department_slug: str,
    template_code: str,
):
    """
    List all API endpoints available for this specific department and template.
    """
    base_url = f"http://localhost:8000/api/v1/{department_slug.lower()}/{template_code.lower()}"
    return APIResponse(data={
        "extract": {
            "method": "POST",
            "url": f"{base_url}",
            "description": "Upload document for extraction."
        },
        "status": {
            "method": "GET",
            "url": f"{base_url}/status/{{job_id}}",
            "description": "Poll extraction status."
        },
        "endpoints": {
            "method": "GET",
            "url": f"{base_url}/endpoints",
            "description": "List all endpoints."
        }
    })

