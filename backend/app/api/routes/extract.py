"""
DOCINT — Department-based Extraction API
Primary extraction endpoints following the pattern: /api/v1/extract/{department_slug}/{template_code}
"""
from __future__ import annotations

import shutil
import uuid

from fastapi import APIRouter, Depends, File, UploadFile, BackgroundTasks
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

logger = get_logger(__name__)

router = APIRouter(tags=["Extraction"])


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
    Returns a job_id you can poll at the status endpoint.
    """
    department_slug = department_slug.upper()
    template_code = template_code.lower()

    # 1. Validate department exists in DB
    dept_stmt = select(Department).where(Department.slug == department_slug)
    dept = db.execute(dept_stmt).scalars().first()
    if not dept:
        raise DocIntError(f"Department '{department_slug}' not found.", "DEPT_NOT_FOUND")

    # 2. Find the active template
    tmpl_stmt = select(Template).where(
        Template.department_id == dept.id,
        Template.code == template_code,
        Template.is_active == 1,
    )
    template = db.execute(tmpl_stmt).scalars().first()
    if not template:
        raise DocIntError(
            f"No active template found for {department_slug}/{template_code}.",
            "TEMPLATE_NOT_FOUND",
        )

    # 3. Validate file
    header_bytes = file.file.read(2048)
    file.file.seek(0)
    file_size = getattr(file, "size", 0)
    if not file_size:
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)

    sanitized_filename = sanitize_original_filename(file.filename or "unnamed")
    mime_type = validate_file(header_bytes, sanitized_filename, file_size)

    # 4. Save file
    stored_filename = generate_stored_filename(mime_type)
    file_path = safe_file_path(settings.storage_path, stored_filename)
    try:
        with open(file_path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)
    except Exception as e:
        raise DocIntError(f"Failed to save file: {str(e)}", "FILE_STORAGE_ERROR")

    # 5. Create Document record
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

    # 6. Create ProcessingJob record
    job_id = str(uuid.uuid4())
    new_job = ProcessingJob(
        id=job_id,
        document_id=doc_id,
        template_id=template.id,
        template_version=template.version,
        status="QUEUED",
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_doc)

    # 7. Dispatch job to Redis worker (graceful fallback to BackgroundTasks if Redis unavailable)
    dispatched_via = "redis"
    try:
        from app.workers.queue import q as redis_queue
        if redis_queue is not None:
            redis_queue.enqueue(
                "app.workers.tasks.process_document_job",
                job_id,        # Passed as positional arg to process_document_job
                job_id=job_id, # Sets the Redis Job ID to match our DB job_id
                job_timeout=settings.JOB_TIMEOUT_SECONDS,
            )
        else:
            raise RuntimeError("Redis queue not initialised")
    except Exception as redis_err:
        logger.warning(
            "redis_dispatch_failed_fallback",
            error=str(redis_err),
            fallback="background_tasks",
        )
        background_tasks.add_task(process_document_job, job_id=job_id)
        dispatched_via = "background_task"

    base_url = f"/api/v1/extract/{department_slug}/{template_code}"
    return APIResponse(data={
        "job_id": job_id,
        "document_id": doc_id,
        "status": "QUEUED",
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

    if job.status == "COMPLETED":
        result_stmt = select(ProcessingResult).where(ProcessingResult.job_id == job_id)
        result = db.execute(result_stmt).scalars().first()
        if result:
            response_data["result"] = result.extracted_json
            response_data["ocr_metadata_json"] = result.ocr_metadata_json
            response_data["confidence_json"] = result.confidence_json

    return APIResponse(data=response_data)

