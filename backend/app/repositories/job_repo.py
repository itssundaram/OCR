from sqlalchemy.orm import Session
from sqlalchemy import select
from .base import BaseRepository
from app.db.models.core import ProcessingJob

class JobRepository(BaseRepository[ProcessingJob]):
    def __init__(self, session: Session):
        super().__init__(ProcessingJob, session)

    # Add custom queries if needed
