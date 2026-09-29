"""
DOCINT — RQ Task Entry Points
RQ resolves task functions by their dotted import path.
Re-export process_document_job here so it can be enqueued as:
    q.enqueue('app.workers.tasks.process_document_job', job_id=...)
"""
from app.services.extractor import process_document_job  # noqa: F401

__all__ = ["process_document_job"]
