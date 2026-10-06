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
from app.repositories.template_repo import TemplateRepository
from app.repositories.department_repo import DepartmentRepository

logger = get_logger(__name__)

router = APIRouter(tags=["Templates"], prefix="/templates")


@router.get("", response_model=APIResponse[list[TemplateResponse]])
def list_templates(department_slug: str | None = None, db: Session = Depends(get_db)):
    """List all templates, optionally filtered by department slug."""
    repo = TemplateRepository(db)
    dept_repo = DepartmentRepository(db)
    
    filters = {}
    if department_slug:
        dept = dept_repo.get_by_slug(department_slug.upper())
        if dept:
            filters["department_id"] = dept.id
        else:
            return APIResponse(data=[])

    # We manually sort because list_all doesn't support complex multi-column sorts out of the box yet
    templates = repo.list_all(filters=filters)
    templates.sort(key=lambda t: (t.department_id, t.code, -t.version))
    
    return APIResponse(data=templates)


@router.get("/{department_slug}/{template_code}", response_model=APIResponse[TemplateResponse])
def get_active_template(department_slug: str, template_code: str, db: Session = Depends(get_db)):
    """Get the active template for a department and template code."""
    repo = TemplateRepository(db)
    dept_repo = DepartmentRepository(db)
    
    dept = dept_repo.get_by_slug(department_slug.upper())
    if not dept:
        raise DocIntError(f"Department {department_slug} not found.", "DEPT_NOT_FOUND")

    template = repo.get_by_department_and_code(dept.id, template_code.lower())
    
    # If no active template, find the latest draft
    if not template:
        drafts = repo.list_all(filters={"department_id": dept.id, "code": template_code.lower()})
        if drafts:
            drafts.sort(key=lambda t: t.version, reverse=True)
            template = drafts[0]

    if not template:
        raise DocIntError(f"No template found for {department_slug}/{template_code}.", "TEMPLATE_NOT_FOUND")

    return APIResponse(data=template)


@router.post("/{department_slug}", response_model=APIResponse[TemplateResponse], status_code=201)
def create_template(department_slug: str, template_in: TemplateCreate, db: Session = Depends(get_db)):
    """Create a new template version for a department."""
    repo = TemplateRepository(db)
    dept_repo = DepartmentRepository(db)
    
    dept = dept_repo.get_by_slug(department_slug.upper())
    if not dept:
        raise DocIntError(f"Department {department_slug} not found.", "DEPT_NOT_FOUND")

    # Find highest version
    existing = repo.list_all(filters={"department_id": dept.id, "code": template_in.code.lower()})
    latest_version = max([t.version for t in existing], default=0)
    new_version = latest_version + 1

    new_template = repo.create({
        "department_id": dept.id,
        "code": template_in.code.lower(),
        "version": new_version,
        "template_json": template_in.template_json,
        "extraction_instructions": template_in.extraction_instructions,
        "is_active": 0  # new templates are always inactive by default
    })
    
    return APIResponse(data=new_template)


@router.post("/{template_id}/activate", response_model=APIResponse[TemplateResponse])
def activate_template(template_id: int, db: Session = Depends(get_db)):
    """
    Activate a template version.
    Automatically deactivates any previously active template for the same department and code.
    """
    repo = TemplateRepository(db)
    template = repo.get_by_id(template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")

    if template.is_active == 1:
        return APIResponse(data=template)

    # Deactivate all others for this department and code
    others = repo.list_all(filters={"department_id": template.department_id, "code": template.code, "is_active": 1})
    for other in others:
        if other.id != template_id:
            repo.update(other.id, {"is_active": 0})

    # Activate this one
    template = repo.update(template_id, {"is_active": 1})
    
    return APIResponse(data=template)


@router.post("/{template_id}/deactivate", response_model=APIResponse[TemplateResponse])
def deactivate_template(template_id: int, db: Session = Depends(get_db)):
    """Deactivate a template version."""
    repo = TemplateRepository(db)
    template = repo.get_by_id(template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")

    template = repo.update(template_id, {"is_active": 0})
    return APIResponse(data=template)


@router.put("/{template_id}", response_model=APIResponse[TemplateResponse])
def update_template(template_id: int, template_in: TemplateUpdate, db: Session = Depends(get_db)):
    """Update a template's JSON schema or code."""
    repo = TemplateRepository(db)
    template = repo.get_by_id(template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")

    update_data = {}
    if template_in.code is not None:
        update_data["code"] = template_in.code.lower()
    if template_in.template_json is not None:
        update_data["template_json"] = template_in.template_json
    if template_in.extraction_instructions is not None:
        update_data["extraction_instructions"] = template_in.extraction_instructions

    if update_data:
        template = repo.update(template_id, update_data)
        
    return APIResponse(data=template)


@router.delete("/{template_id}", response_model=APIResponse[dict])
def delete_template(template_id: int, db: Session = Depends(get_db)):
    """Delete a template version."""
    repo = TemplateRepository(db)
    template = repo.get_by_id(template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")

    # Manually delete associated processing jobs to avoid FK violation
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

    repo.delete(template_id)
    
    return APIResponse(data={"success": True})
