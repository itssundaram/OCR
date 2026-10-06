from typing import Generic, TypeVar, Type, Optional, List, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, update, delete

T = TypeVar("T")

class BaseRepository(Generic[T]):
    """
    Generic repository base.

    NOTE: every route module in app/api/routes/ (departments, documents,
    processing, templates) was written against a wider contract than this
    class originally implemented — list_all(filters=...), get_by_id(id),
    and an id-based update(id, dict) — none of which existed here. That
    mismatch was a real, pre-existing bug (AttributeError on first live
    call), not something introduced by the frontend rebuild; it just hadn't
    been exercised end-to-end until now. Fixed by implementing the contract
    every caller already assumes, rather than touching a dozen call sites.
    """

    def __init__(self, model_cls: Type[T], session: Session):
        self.model_cls = model_cls
        self.session = session

    def get(self, id: Any) -> Optional[T]:
        return self.session.get(self.model_cls, id)

    def get_by_id(self, id: Any) -> Optional[T]:
        return self.get(id)

    def get_all(self, skip: int = 0, limit: int = 100) -> List[T]:
        stmt = select(self.model_cls).offset(skip).limit(limit)
        return self.session.execute(stmt).scalars().all()

    def list_all(self, filters: dict | None = None, skip: int = 0, limit: int = 1000) -> List[T]:
        stmt = select(self.model_cls)
        if filters:
            for key, value in filters.items():
                stmt = stmt.where(getattr(self.model_cls, key) == value)
        stmt = stmt.offset(skip).limit(limit)
        return self.session.execute(stmt).scalars().all()

    def create(self, obj_in: dict | T) -> T:
        if isinstance(obj_in, dict):
            obj = self.model_cls(**obj_in)
        else:
            obj = obj_in
        self.session.add(obj)
        self.session.commit()
        self.session.refresh(obj)
        return obj

    def update(self, id_or_obj: Any, obj_in: dict) -> Optional[T]:
        """Accepts either a primary key (the convention every route uses) or
        an already-loaded model instance (the original signature), so both
        call styles in the codebase work."""
        if isinstance(id_or_obj, self.model_cls):
            db_obj = id_or_obj
        else:
            db_obj = self.get(id_or_obj)
            if db_obj is None:
                return None
        for key, value in obj_in.items():
            setattr(db_obj, key, value)
        self.session.add(db_obj)
        self.session.commit()
        self.session.refresh(db_obj)
        return db_obj

    def delete(self, id: Any) -> bool:
        obj = self.get(id)
        if obj:
            self.session.delete(obj)
            self.session.commit()
            return True
        return False
