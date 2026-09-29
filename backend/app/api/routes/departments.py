"""
DOCINT — Departments API
CRUD endpoints for managing departments stored in the database.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import Department
from app.schemas.common import APIResponse
from app.core.exceptions import DocIntError
from pydantic import BaseModel


router = APIRouter(tags=["Departments"], prefix="/departments")


class DepartmentCreate(BaseModel):
    slug: str
    name: str
    description: str | None = None
    color: str = "#6366f1"
    icon: str = "folder"


class DepartmentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    icon: str | None = None


class DepartmentResponse(BaseModel):
    id: int
    slug: str
    name: str
    description: str | None
    color: str
    icon: str
    is_active: int

    model_config = {"from_attributes": True}


@router.get("", response_model=APIResponse[list[DepartmentResponse]])
def list_departments(db: Session = Depends(get_db)):
    """List all active departments."""
    stmt = select(Department).where(Department.is_active == 1).order_by(Department.name)
    departments = db.execute(stmt).scalars().all()
    return APIResponse(data=departments)


@router.get("/all", response_model=APIResponse[list[DepartmentResponse]])
def list_all_departments(db: Session = Depends(get_db)):
    """List all departments including inactive."""
    stmt = select(Department).order_by(Department.name)
    departments = db.execute(stmt).scalars().all()
    return APIResponse(data=departments)


@router.post("", response_model=APIResponse[DepartmentResponse], status_code=201)
def create_department(dept_in: DepartmentCreate, db: Session = Depends(get_db)):
    """Create a new department."""
    stmt = select(Department).where(Department.slug == dept_in.slug.upper())
    existing = db.execute(stmt).scalars().first()
    if existing:
        raise DocIntError(f"Department with slug {dept_in.slug} already exists.", "DEPT_ALREADY_EXISTS")

    new_dept = Department(
        slug=dept_in.slug.upper(),
        name=dept_in.name,
        description=dept_in.description,
        color=dept_in.color,
        icon=dept_in.icon,
        is_active=1,
    )
    db.add(new_dept)
    db.commit()
    db.refresh(new_dept)
    return APIResponse(data=new_dept)


@router.patch("/{slug}", response_model=APIResponse[DepartmentResponse])
def update_department(slug: str, updates: DepartmentUpdate, db: Session = Depends(get_db)):
    """Update department metadata."""
    stmt = select(Department).where(Department.slug == slug.upper())
    dept = db.execute(stmt).scalars().first()
    if not dept:
        raise DocIntError(f"Department {slug} not found.", "DEPT_NOT_FOUND")

    if updates.name is not None:
        dept.name = updates.name
    if updates.description is not None:
        dept.description = updates.description
    if updates.color is not None:
        dept.color = updates.color
    if updates.icon is not None:
        dept.icon = updates.icon

    db.commit()
    db.refresh(dept)
    return APIResponse(data=dept)


@router.post("/{slug}/deactivate", response_model=APIResponse[DepartmentResponse])
def deactivate_department(slug: str, db: Session = Depends(get_db)):
    """Deactivate a department."""
    stmt = select(Department).where(Department.slug == slug.upper())
    dept = db.execute(stmt).scalars().first()
    if not dept:
        raise DocIntError(f"Department {slug} not found.", "DEPT_NOT_FOUND")
    dept.is_active = 0
    db.commit()
    db.refresh(dept)
    return APIResponse(data=dept)


@router.post("/seed", response_model=APIResponse[list[DepartmentResponse]], status_code=201)
def seed_departments(db: Session = Depends(get_db)):
    """
    Seed the database with the 6 standard departments.
    Safe to call multiple times — skips any slugs that already exist.
    """
    defaults = [
        {"slug": "TMS",     "name": "TMS",     "description": "TMS Documents",     "color": "#3b82f6", "icon": "folder"},
        {"slug": "PSG",     "name": "PSG",     "description": "PSG Documents",     "color": "#10b981", "icon": "folder"},
        {"slug": "MMS",     "name": "MMS",     "description": "MMS Documents",     "color": "#8b5cf6", "icon": "folder"},
        {"slug": "CUE_TMS", "name": "CUE_TMS", "description": "CUE TMS Documents", "color": "#f59e0b", "icon": "folder"},
        {"slug": "CUE_PSG", "name": "CUE_PSG", "description": "CUE PSG Documents", "color": "#ef4444", "icon": "folder"},
        {"slug": "CUE_MMS", "name": "CUE_MMS", "description": "CUE MMS Documents", "color": "#6366f1", "icon": "folder"},
    ]
    created = []
    for d in defaults:
        existing = db.execute(select(Department).where(Department.slug == d["slug"])).scalars().first()
        if not existing:
            new_dept = Department(
                slug=d["slug"], name=d["name"], description=d["description"],
                color=d["color"], icon=d["icon"], is_active=1,
            )
            db.add(new_dept)
            db.flush()
            created.append(new_dept)
    db.commit()
    for dept in created:
        db.refresh(dept)
    return APIResponse(data=created)
