from sqlalchemy.orm import Session
from sqlalchemy import select
from .base import BaseRepository
from app.db.models.core import Document

class DocumentRepository(BaseRepository[Document]):
    def __init__(self, session: Session):
        super().__init__(Document, session)

    def list_all(self, filters: dict | None = None, skip: int = 0, limit: int = 1000, order_by=None):
        """Documents of prefetched, not yet executed jobs stay hidden (see app/services/prefetch.py)."""
        from app.db.models.core import ProcessingJob
        from app.services.prefetch import HIDDEN
        stmt = select(Document).where(Document.id.notin_(select(ProcessingJob.document_id).where(ProcessingJob.error_code.in_(HIDDEN))))
        for key, value in (filters or {}).items():
            stmt = stmt.where(getattr(Document, key) == value)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        return self.session.execute(stmt.offset(skip).limit(limit)).scalars().all()
