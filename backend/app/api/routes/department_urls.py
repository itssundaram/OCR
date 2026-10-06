"""
DOCINT — Generate URL API

Backs the "Generate URL" sidebar page: produces a shareable, pre-filled link
into the OCR single-pipeline upload page for a given department + (optional)
template, so a link can be handed to a specific team without them having to
navigate department/template dropdowns themselves. Internal-only — DOCINT has
no authentication layer (explicit product decision), so this is a convenience
link, not an access-controlled public endpoint; it only ever points back into
this same app's own /ocr route.

The DEPARTMENT_URLS table already existed in the schema (Phase 2) but had no
route built against it until now.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from pydantic import BaseModel

from app.db.database import get_db
from app.db.models import DepartmentUrl, Department, Template
from app.schemas.common import APIResponse
from app.core.exceptions import DocIntError

router = APIRouter(tags=["Generate URL"], prefix="/department-urls")


class DepartmentUrlCreate(BaseModel):
    department_id: int
    template_id: int | None = None
    label: str | None = None


class DepartmentUrlResponse(BaseModel):
    id: int
    department_id: int
    department_slug: str | None = None
    department_name: str | None = None
    template_id: int | None
    template_code: str | None = None
    url: str
    label: str | None
    is_active: bool
    last_checked_at: object | None = None
    last_status: str | None = None
    created_at: object

    model_config = {"from_attributes": True}


def _serialize(row: DepartmentUrl, dept: Department | None, template: Template | None) -> dict:
    return {
        "id": row.id,
        "department_id": row.department_id,
        "department_slug": dept.slug if dept else None,
        "department_name": dept.name if dept else None,
        "template_id": row.template_id,
        "template_code": template.code if template else None,
        "url": row.url,
        "label": row.label,
        "is_active": bool(row.is_active),
        "last_checked_at": row.last_checked_at,
        "last_status": row.last_status,
        "created_at": row.created_at,
    }


@router.post("", response_model=APIResponse)
def create_department_url(payload: DepartmentUrlCreate, db: Session = Depends(get_db)):
    dept = db.get(Department, payload.department_id)
    if not dept:
        raise DocIntError(f"Department {payload.department_id} not found.", "DEPARTMENT_NOT_FOUND")

    template = None
    if payload.template_id is not None:
        template = db.get(Template, payload.template_id)
        if not template:
            raise DocIntError(f"Template {payload.template_id} not found.", "TEMPLATE_NOT_FOUND")

    token = secrets.token_urlsafe(8)
    query = f"department={dept.slug}"
    if template:
        query += f"&template={template.code}"
    query += f"&ref={token}"
    relative_url = f"/ocr?{query}"

    row = DepartmentUrl(
        department_id=dept.id,
        template_id=template.id if template else None,
        url=relative_url,
        label=payload.label,
        is_active=1,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return APIResponse(data=_serialize(row, dept, template))


@router.get("", response_model=APIResponse)
def list_department_urls(department_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(DepartmentUrl).order_by(desc(DepartmentUrl.id))
    if department_id is not None:
        stmt = stmt.where(DepartmentUrl.department_id == department_id)
    rows = db.execute(stmt).scalars().all()

    dept_ids = {r.department_id for r in rows}
    template_ids = {r.template_id for r in rows if r.template_id}
    depts = {d.id: d for d in db.execute(select(Department).where(Department.id.in_(dept_ids))).scalars()} if dept_ids else {}
    templates = {t.id: t for t in db.execute(select(Template).where(Template.id.in_(template_ids))).scalars()} if template_ids else {}

    return APIResponse(data=[
        _serialize(r, depts.get(r.department_id), templates.get(r.template_id)) for r in rows
    ])


@router.post("/{url_id}/deactivate", response_model=APIResponse)
def deactivate_department_url(url_id: int, db: Session = Depends(get_db)):
    row = db.get(DepartmentUrl, url_id)
    if not row:
        raise DocIntError(f"Generated URL {url_id} not found.", "URL_NOT_FOUND")
    row.is_active = 0
    db.commit()
    db.refresh(row)
    dept = db.get(Department, row.department_id)
    template = db.get(Template, row.template_id) if row.template_id else None
    return APIResponse(data=_serialize(row, dept, template))


@router.delete("/{url_id}", response_model=APIResponse)
def delete_department_url(url_id: int, db: Session = Depends(get_db)):
    row = db.get(DepartmentUrl, url_id)
    if not row:
        raise DocIntError(f"Generated URL {url_id} not found.", "URL_NOT_FOUND")
    db.delete(row)
    db.commit()
    return APIResponse(data={"deleted": True, "id": url_id})
