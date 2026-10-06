"""
DOCINT — RQ Task Entry Points
RQ resolves task functions by their dotted import path.
Re-export each entry point here so it can be enqueued as:
    q.enqueue('app.workers.tasks.process_document_job', job_id=...)
"""
from app.services.extractor import process_document_job, mark_job_permanently_failed  # noqa: F401
from app.services.orchestrator import run_single_pipeline_job, run_pipeline_comparison_job  # noqa: F401
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "process_document_job",
    "run_single_pipeline_job",
    "run_pipeline_comparison_job",
    "job_failed_callback",
]


def job_failed_callback(job, connection, type, value, traceback) -> None:  # noqa: A002 - RQ's fixed signature
    """RQ on_failure hook (Phase 10, extended in Phase 13 to cover the two
    new orchestrator entry points too). Fires only once a job's bounded
    Retry policy (set at enqueue time in app/api/routes/extract.py) is
    exhausted, or on an error that bypassed the task function's own
    try/except entirely. Marks our Oracle-backed ProcessingJob row FAILED —
    without this, such a job would leave that row stuck at PROCESSING
    forever.
    """
    business_job_id = None
    try:
        if job.args:
            business_job_id = job.args[0]
        elif job.kwargs:
            business_job_id = job.kwargs.get("job_id")
    except Exception:
        business_job_id = None

    if not business_job_id:
        logger.error("job_failed_callback_missing_job_id", rq_job_id=getattr(job, "id", None))
        return

    try:
        mark_job_permanently_failed(business_job_id, str(value))
    except Exception:
        logger.error("job_failed_callback_mark_failed_errored", job_id=business_job_id, exc_info=True)
