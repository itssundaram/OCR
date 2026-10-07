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

import shutil
import uuid

from fastapi import APIRouter, Depends, File, UploadFile, BackgroundTasks, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import Department, Document, Template, ProcessingJob, ProcessingResult
from app.schemas.common import APIResponse
from app.core.config import settings
from app.core.exceptions import DocIntError
from app.core.logging import get_logger
from app.core.security import (
    validate_file,
    generate_stored_filename,
    safe_file_path,
    sanitize_original_filename,
)
from app.services.extractor import process_document_job
from app.services.orchestrator import run_single_pipeline_job, run_pipeline_comparison_job
from app.pipelines.registry import list_pipelines, get_pipeline

logger = get_logger(__name__)

router = APIRouter(tags=["Extraction"])


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
    db: Session = Depends(get_db),
):
    """
    OCR page (Phase 13): upload with ONE explicitly chosen pipeline. Only
    that pipeline processes the document — see
    app/services/orchestrator.py::run_single_pipeline_job.
    """
    if pipeline_name not in list_pipelines():
        raise DocIntError(
            f"Unknown pipeline '{pipeline_name}'. Available: {', '.join(list_pipelines())}",
            "PIPELINE_NOT_FOUND",
        )

    template = _resolve_department_and_template(db, department_slug, template_code)
    doc_id, job_id = _save_uploaded_file_and_create_job(db, file, template)

    dispatched_via = _dispatch_job(
        "app.workers.tasks.run_single_pipeline_job", job_id, (pipeline_name,),
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
    pipelines: list[str] = Query(..., description="Pipeline names to run and compare, e.g. ?pipelines=surya&pipelines=qwen"),
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

