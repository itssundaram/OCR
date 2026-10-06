from sqlalchemy.orm import Session
from .base import BaseRepository
from app.db.models.core import Department

class DepartmentRepository(BaseRepository[Department]):
    def __init__(self, session: Session):
        super().__init__(Department, session)

    def get_by_slug(self, slug: str) -> Department | None:
        from sqlalchemy import select
        stmt = select(Department).where(Department.slug == slug)
        return self.session.execute(stmt).scalar_one_or_none()
