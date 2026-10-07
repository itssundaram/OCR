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
from app.repositories.job_repo import JobRepository

router = APIRouter(tags=["Processing Jobs"], prefix="/processing")


@router.get("/{job_id}", response_model=APIResponse)
def get_processing_job(job_id: str, db: Session = Depends(get_db)):
    """
    Get the status of a processing job.
    If completed, also returns the extracted results.
    """
    repo = JobRepository(db)
    job = repo.get_by_id(job_id)
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
        "overall_confidence": job.overall_confidence,
        "pipeline_mode": job.pipeline_mode
    }
    
    # Phase 10 introduced "WARNING" as a real completion state (finished, but
    # with fields needing review) — must still surface its result, same fix
    # as app/api/routes/extract.py's own status endpoint.
    if job.status in ("COMPLETED", "WARNING"):
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
    repo = JobRepository(db)
    filters = {}
    if document_id:
        filters["document_id"] = document_id
        
    jobs = repo.list_all(filters=filters, skip=skip, limit=limit)
    
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

from app.db.models import ProcessingEvent, PipelineRun
from app.core.exceptions import DocIntError

@router.get("/{job_id}/pipeline-steps", response_model=APIResponse)
def get_pipeline_steps(job_id: str, db: Session = Depends(get_db)):
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise DocIntError(f"Processing job {job_id} not found.", "JOB_NOT_FOUND")

    stmt_runs = select(PipelineRun).where(PipelineRun.job_id == job_id)
    runs = db.execute(stmt_runs).scalars().all()
    
    stmt_events = select(ProcessingEvent).where(ProcessingEvent.job_id == job_id).order_by(ProcessingEvent.created_at)
    events = db.execute(stmt_events).scalars().all()
    
    pipelines = {}
    for run in runs:
        if run.pipeline_name == "consensus":
            continue
        pipelines[run.pipeline_name] = {
            "overall_status": run.status,
            "overall_confidence": run.overall_confidence,
            "steps": []
        }
    
    for event in events:
        run_id = event.pipeline_run_id
        if not run_id:
            continue
        run = next((r for r in runs if r.id == run_id), None)
        if not run or run.pipeline_name == "consensus":
            continue
            
        p_name = run.pipeline_name
        meta = event.metadata_json or {}
        step_name = event.event_type
        if step_name and step_name not in ("PIPELINE_STARTED", "PIPELINE_COMPLETED"):
            pipelines[p_name]["steps"].append({
                "name": step_name,
                "status": event.status or "DONE",
                "data": meta
            })
            
    return APIResponse(data={"pipelines": pipelines})
