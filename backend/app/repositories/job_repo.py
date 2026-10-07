from sqlalchemy.orm import Session
from sqlalchemy import select
from .base import BaseRepository
from app.db.models.core import ProcessingJob

class JobRepository(BaseRepository[ProcessingJob]):
    def __init__(self, session: Session):
        super().__init__(ProcessingJob, session)

    def list_all(self, filters: dict | None = None, skip: int = 0, limit: int = 1000):
        stmt = select(self.model_cls)
        if filters:
            for key, value in filters.items():
                stmt = stmt.where(getattr(self.model_cls, key) == value)
        stmt = stmt.order_by(self.model_cls.created_at.desc()).offset(skip).limit(limit)
        return self.session.execute(stmt).scalars().all()
