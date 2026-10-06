from sqlalchemy.orm import Session
from sqlalchemy import select
from .base import BaseRepository
from app.db.models.core import Template

class TemplateRepository(BaseRepository[Template]):
    def __init__(self, session: Session):
        super().__init__(Template, session)

    def get_by_department_and_code(self, department_id: int, code: str) -> Template | None:
        stmt = select(Template).where(
            Template.department_id == department_id,
            Template.code == code,
            Template.is_active == 1
        )
        return self.session.execute(stmt).scalar_one_or_none()
