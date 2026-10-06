"""
DOCINT — Processing Events API (Phase 14 of the implementation plan)

Surfaces the PROCESSING_EVENTS rows that app/services/extractor.py and
app/services/orchestrator.py already write on every job-state transition
(DOCUMENT_UPLOADED, PAGE_RENDERED, PIPELINE_STARTED/COMPLETED, FIELD_NEEDS_REVIEW,
DOCUMENT_COMPLETED, etc. — see _record_event in app/services/extractor.py).

Scope note: this is a REST list endpoint, polled the same way the frontend
already polls /processing/{job_id} — not a WebSocket/Redis-pub-sub push
channel. The plan's Phase 14 flags pub/sub as "no new infrastructure needed"
since Redis is already in place, but that is additional async-code surface
area this environment cannot exercise against a live worker before the GPU
pass (no Redis/RQ integration test can run here). A REST feed is lower risk,
gives the Phase 22 Logs UI and per-job timelines everything they need today,
and the event rows are already being written — a push channel can be layered
on top of this same table later without changing anything here.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, desc

from app.db.database import get_db
from app.db.models import ProcessingEvent, ProcessingJob
from app.schemas.common import APIResponse
from app.core.exceptions import DocIntError

router = APIRouter(tags=["Events"])


def _serialize(event: ProcessingEvent, document_id: str | None = None) -> dict:
    return {
        "id": event.id,
        "job_id": event.job_id,
        "document_id": document_id,
        "pipeline_run_id": event.pipeline_run_id,
        "page_id": event.page_id,
        "event_type": event.event_type,
        "status": event.status,
        "message": event.message,
        "metadata": event.metadata_json,
        "duration_ms": event.duration_ms,
        "created_at": event.created_at,
    }


@router.get("/processing/{job_id}/events", response_model=APIResponse)
def list_job_events(job_id: str, db: Session = Depends(get_db)):
    """Full event timeline for one job, oldest first — the audit trail for a
    single document's processing (what Phase 22's Logs UI drills into)."""
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise DocIntError(f"Processing job {job_id} not found.", "JOB_NOT_FOUND")

    stmt = (
        select(ProcessingEvent)
        .where(ProcessingEvent.job_id == job_id)
        .order_by(ProcessingEvent.id)
    )
    events = db.execute(stmt).scalars().all()
    return APIResponse(data=[_serialize(e, job.document_id) for e in events])


@router.get("/events/recent", response_model=APIResponse)
def list_recent_events(limit: int = 100, event_type: str | None = None, status: str | None = None, db: Session = Depends(get_db)):
    """Most recent events across ALL jobs, newest first — the system-wide
    activity feed for Phase 22's Logs/observability screen."""
    limit = max(1, min(limit, 500))
    stmt = select(ProcessingEvent).order_by(desc(ProcessingEvent.id)).limit(limit)
    if event_type:
        stmt = stmt.where(ProcessingEvent.event_type == event_type)
    if status:
        stmt = stmt.where(ProcessingEvent.status == status)
    events = db.execute(stmt).scalars().all()
    job_ids = {e.job_id for e in events}
    doc_id_by_job = {}
    if job_ids:
        jobs_stmt = select(ProcessingJob.id, ProcessingJob.document_id).where(ProcessingJob.id.in_(job_ids))
        doc_id_by_job = {jid: did for jid, did in db.execute(jobs_stmt).all()}
    return APIResponse(data=[_serialize(e, doc_id_by_job.get(e.job_id)) for e in events])
