"""
DOCINT — Dashboard Route
Provides aggregated statistics for the frontend dashboard.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.db.database import get_db
from app.db.models import Document, ProcessingJob
from app.schemas.common import APIResponse

router = APIRouter(tags=['Dashboard'])

@router.get('/dashboard/stats', response_model=APIResponse)
def dashboard_stats(db: Session = Depends(get_db)):
    # Total documents
    total_docs = db.scalar(select(func.count(Document.id))) or 0
    
    # Processed jobs stats
    stmt_jobs = select(
        ProcessingJob.status, 
        func.count(ProcessingJob.id),
        func.avg(ProcessingJob.overall_confidence)
    ).group_by(ProcessingJob.status)
    
    job_stats = db.execute(stmt_jobs).all()
    
    completed_jobs = 0
    failed_jobs = 0
    in_progress_jobs = 0
    total_confidence = 0
    confidence_count = 0
    
    for status, count, avg_conf in job_stats:
        if status == 'COMPLETED':
            completed_jobs += count
            if avg_conf is not None:
                total_confidence += avg_conf * count
                confidence_count += count
        elif status == 'FAILED':
            failed_jobs += count
        else:
            in_progress_jobs += count
            
    avg_confidence = (total_confidence / confidence_count) if confidence_count > 0 else 0
    
    return APIResponse(data={
        "total_documents": total_docs,
        "completed_jobs": completed_jobs,
        "failed_jobs": failed_jobs,
        "in_progress_jobs": in_progress_jobs,
        "average_confidence": round(avg_confidence, 4)
    })
