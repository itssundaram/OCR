"""
DOCINT — Processing Job Route
Retrieve statuses and results of queued background jobs.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import ProcessingJob, ProcessingResult
from app.schemas.common import APIResponse
from app.core.exceptions import DocIntError

router = APIRouter(tags=["Processing Jobs"], prefix="/processing")


@router.get("/{job_id}", response_model=APIResponse)
def get_processing_job(job_id: str, db: Session = Depends(get_db)):
    """
    Get the status of a processing job.
    If completed, also returns the extracted results.
    """
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise DocIntError(f"Processing job {job_id} not found.", "JOB_NOT_FOUND")
        
    response_data = {
        "job_id": job.id,
        "document_id": job.document_id,
        "status": job.status,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "error_message": job.error_message,
        "ai_engine": job.ai_engine,
        "ai_model": job.ai_model,
        "overall_confidence": job.overall_confidence
    }
    
    if job.status == "COMPLETED":
        stmt_result = select(ProcessingResult).where(ProcessingResult.job_id == job_id)
        result = db.execute(stmt_result).scalars().first()
        if result:
            response_data["extracted_json"] = result.extracted_json
            response_data["confidence_json"] = result.confidence_json
            response_data["ocr_metadata_json"] = result.ocr_metadata_json
            response_data["validation_warnings"] = result.validation_warnings
            
    return APIResponse(data=response_data)


@router.get("/", response_model=APIResponse)
def list_processing_jobs(
    skip: int = 0, 
    limit: int = 100, 
    document_id: str | None = None,
    db: Session = Depends(get_db)
):
    """
    List processing jobs, optionally filtered by document_id.
    """
    stmt = select(ProcessingJob)
    if document_id:
        stmt = stmt.where(ProcessingJob.document_id == document_id)
        
    stmt = stmt.order_by(ProcessingJob.created_at.desc()).offset(skip).limit(limit)
    
    jobs = db.execute(stmt).scalars().all()
    
    data = []
    for job in jobs:
        data.append({
            "job_id": job.id,
            "document_id": job.document_id,
            "status": job.status,
            "created_at": job.created_at,
            "overall_confidence": job.overall_confidence
        })
        
    return APIResponse(data=data)
