from sqlalchemy.orm import Session
from sqlalchemy import select
from .base import BaseRepository
from app.db.models.core import Document

class DocumentRepository(BaseRepository[Document]):
    def __init__(self, session: Session):
        super().__init__(Document, session)

    # Add custom queries if needed
