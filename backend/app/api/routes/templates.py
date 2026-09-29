"""
DOCINT — Document Templates API
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from app.db.database import get_db
from app.db.models import Template, Department, ProcessingJob, Document
from app.schemas.common import APIResponse
from app.schemas.templates import TemplateCreate, TemplateUpdate, TemplateResponse
from app.core.exceptions import DocIntError
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["Templates"], prefix="/templates")


@router.get("", response_model=APIResponse[list[TemplateResponse]])
def list_templates(department_slug: str | None = None, db: Session = Depends(get_db)):
    """List all templates, optionally filtered by department slug."""
    stmt = select(Template)
    if department_slug:
        stmt_dept = select(Department.id).where(Department.slug == department_slug.upper())
        dept_id = db.execute(stmt_dept).scalars().first()
        if dept_id:
            stmt = stmt.where(Template.department_id == dept_id)
        else:
            return APIResponse(data=[])

    stmt = stmt.order_by(Template.department_id, Template.code, Template.version.desc())
    templates = db.execute(stmt).scalars().all()
    return APIResponse(data=templates)


@router.get("/{department_slug}/{template_code}", response_model=APIResponse[TemplateResponse])
def get_active_template(department_slug: str, template_code: str, db: Session = Depends(get_db)):
    """Get the active template for a department and template code."""
    # Find department
    stmt_dept = select(Department).where(Department.slug == department_slug.upper())
    dept = db.execute(stmt_dept).scalars().first()
    if not dept:
        raise DocIntError(f"Department {department_slug} not found.", "DEPT_NOT_FOUND")

    # Find active template first
    stmt = select(Template).where(
        Template.department_id == dept.id,
        Template.code == template_code.lower(),
        Template.is_active == 1
    )
    template = db.execute(stmt).scalars().first()
    
    # If no active template, find the latest draft
    if not template:
        stmt_draft = select(Template).where(
            Template.department_id == dept.id,
            Template.code == template_code.lower()
        ).order_by(Template.version.desc())
        template = db.execute(stmt_draft).scalars().first()

    if not template:
        raise DocIntError(f"No template found for {department_slug}/{template_code}.", "TEMPLATE_NOT_FOUND")

    return APIResponse(data=template)


@router.post("/{department_slug}", response_model=APIResponse[TemplateResponse], status_code=201)
def create_template(department_slug: str, template_in: TemplateCreate, db: Session = Depends(get_db)):
    """Create a new template version for a department."""
    stmt_dept = select(Department).where(Department.slug == department_slug.upper())
    dept = db.execute(stmt_dept).scalars().first()
    if not dept:
        raise DocIntError(f"Department {department_slug} not found.", "DEPT_NOT_FOUND")

    # Find highest version for this template code
    stmt = select(Template.version).where(
        Template.department_id == dept.id,
        Template.code == template_in.code.lower()
    ).order_by(Template.version.desc())
    latest_version = db.execute(stmt).scalars().first()
    new_version = (latest_version or 0) + 1

    new_template = Template(
        department_id=dept.id,
        code=template_in.code.lower(),
        version=new_version,
        template_json=template_in.template_json,
        extraction_instructions=template_in.extraction_instructions,
        is_active=0  # new templates are always inactive by default
    )
    db.add(new_template)
    db.commit()
    db.refresh(new_template)
    return APIResponse(data=new_template)


@router.post("/{template_id}/activate", response_model=APIResponse[TemplateResponse])
def activate_template(template_id: int, db: Session = Depends(get_db)):
    """
    Activate a template version.
    Automatically deactivates any previously active template for the same department and code.
    """
    template = db.get(Template, template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")

    if template.is_active == 1:
        return APIResponse(data=template)  # Already active

    # Deactivate all others for this department and code
    stmt_deactivate = (
        update(Template)
        .where(
            Template.department_id == template.department_id,
            Template.code == template.code,
            Template.id != template_id
        )
        .values(is_active=0)
    )
    db.execute(stmt_deactivate)

    # Activate this one
    template.is_active = 1

    db.commit()
    db.refresh(template)
    return APIResponse(data=template)


@router.post("/{template_id}/deactivate", response_model=APIResponse[TemplateResponse])
def deactivate_template(template_id: int, db: Session = Depends(get_db)):
    """Deactivate a template version."""
    template = db.get(Template, template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")

    template.is_active = 0
    db.commit()
    db.refresh(template)
    return APIResponse(data=template)


@router.put("/{template_id}", response_model=APIResponse[TemplateResponse])
def update_template(template_id: int, template_in: TemplateUpdate, db: Session = Depends(get_db)):
    """Update a template's JSON schema or code."""
    template = db.get(Template, template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")

    if template_in.code is not None:
        template.code = template_in.code.lower()
    if template_in.template_json is not None:
        template.template_json = template_in.template_json
    if template_in.extraction_instructions is not None:
        template.extraction_instructions = template_in.extraction_instructions

    db.commit()
    db.refresh(template)
    return APIResponse(data=template)


@router.delete("/{template_id}", response_model=APIResponse[dict])
def delete_template(template_id: int, db: Session = Depends(get_db)):
    """Delete a template version."""
    template = db.get(Template, template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")

    # Manually delete associated processing jobs to avoid FK violation
    # SQLAlchemy will also cascade delete the ProcessingResult for each job
    stmt_jobs = select(ProcessingJob).where(ProcessingJob.template_id == template_id)
    jobs = db.execute(stmt_jobs).scalars().all()
    
    document_ids = [job.document_id for job in jobs]
    
    for job in jobs:
        db.delete(job)
        
    if document_ids:
        stmt_docs = select(Document).where(Document.id.in_(document_ids))
        docs = db.execute(stmt_docs).scalars().all()
        for doc in docs:
            doc.status = "PENDING"

    db.delete(template)
    db.commit()
    return APIResponse(data={"success": True})
