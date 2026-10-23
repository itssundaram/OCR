"""
DOCINT — Document Templates API
"""
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import select, update
from sqlalchemy.orm.attributes import flag_modified

from app.db.database import get_db
from app.db.models import Template, Department, ProcessingJob, Document, ProcessingResult
from app.anchors import (
    AnchorConflict, merge_anchor_fields, rollback as rollback_anchors, words_from_detections,
)
from app.schemas.common import APIResponse
from app.schemas.templates import TemplateCreate, TemplateUpdate, TemplateResponse
from app.core.exceptions import DocIntError
from app.core.logging import get_logger
from app.repositories.template_repo import TemplateRepository
from app.repositories.department_repo import DepartmentRepository

logger = get_logger(__name__)

router = APIRouter(tags=["Templates"], prefix="/templates")


class LockAnchorsRequest(BaseModel):
    fields: dict[str, Any]
    page_width: int | None = 1654
    page_height: int | None = 2338
    # Optional: the job these boxes were confirmed on. When given, the printed
    # label next to each box and the confirmed value's shape are learned from
    # that job's OCR words so the anchor can be read back on the next run.
    job_id: str | None = None
    # Optional optimistic-lock: version of the anchors the caller last saw.
    expected_version: int | None = None


def job_words_by_page(db: Session, job_id: str | None) -> list[list[dict]] | None:
    """Per-page OCR words stored with a finished job (None if unavailable)."""
    if not job_id:
        return None
    try:
        res = db.execute(
            select(ProcessingResult).where(ProcessingResult.job_id == job_id)
        ).scalars().first()
        meta = dict(getattr(res, "ocr_metadata_json", None) or {})
        dets = meta.get("all_detections") or []
        return words_from_detections(dets) if dets else None
    except Exception as exc:  # enrichment is best-effort, never block a save
        logger.warning("job_words_load_failed", job_id=job_id, error=str(exc))
        return None


@router.post("/{template_id}/lock-anchors", response_model=APIResponse[dict])
def lock_template_anchors(template_id: int, req: LockAnchorsRequest, db: Session = Depends(get_db)):
    """
    Commit human-verified ground-truth boxes to the template's learned_anchors.

    Fields are MERGED into what is already stored (fields not in this request are
    kept), each is enriched with page number / page-relative box / printed label /
    value shape, and the anchor set gets a new version. Sets is_verified: true so
    automatic cold-path guesses never overwrite it. Can be called again anytime.
    """
    repo = TemplateRepository(db)
    template = repo.get_by_id(template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")

    tj = dict(template.template_json or {})
    try:
        learned_anchors, report = merge_anchor_fields(
            tj.get("learned_anchors"),
            req.fields,
            page_w=req.page_width or 1654,
            page_h=req.page_height or 2338,
            verified=True,
            words_by_page=job_words_by_page(db, req.job_id),
            expected_version=req.expected_version,
            source="user_lock",
        )
    except AnchorConflict as exc:
        raise DocIntError(
            f"Anchors were changed by someone else (now v{exc.current}); reload and try again.",
            "ANCHOR_VERSION_CONFLICT",
        )
    tj["learned_anchors"] = learned_anchors
    # every confirmed field teaches the template the label it is printed under ("From" for a field called unit)
    learned_aliases: list[dict] = []
    try:
        from app.ocr.label_kb import learn_aliases_from_anchors
        fields_def = [dict(f) for f in (tj.get("fields") or []) if isinstance(f, dict)]
        learned_aliases = learn_aliases_from_anchors(fields_def, learned_anchors.get("fields") or {},
                                                     only=list((req.fields or {}).keys()))
        if learned_aliases:
            by_name = {f.get("name"): f for f in fields_def}
            tj["fields"] = [by_name.get(f.get("name"), f) if isinstance(f, dict) else f for f in tj.get("fields") or []]
    except Exception as exc:          # learning is a bonus; the lock itself must always succeed
        logger.warning("alias_learning_failed", template_id=template_id, error=str(exc))
    template = repo.update(template_id, {"template_json": tj})

    logger.info("template_anchors_locked", template_id=template_id, version=report["version"],
                added=len(report["added"]), updated=len(report["updated"]), kept=len(report["kept"]))
    return APIResponse(data={
        "message": "Template ground-truth anchors locked successfully.",
        "template_id": template_id,
        "learned_anchors": {k: v for k, v in learned_anchors.items() if k != "history"},
        "report": report,
        "learned_aliases": learned_aliases,
    })


@router.post("/{template_id}/rollback-anchors", response_model=APIResponse[dict])
def rollback_template_anchors(template_id: int, to_version: int | None = None, db: Session = Depends(get_db)):
    """Restore an earlier version of the anchors (default: the one just before the latest save)."""
    repo = TemplateRepository(db)
    template = repo.get_by_id(template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")
    tj = dict(template.template_json or {})
    try:
        la = rollback_anchors(tj.get("learned_anchors"), to_version)
    except ValueError as exc:
        raise DocIntError(str(exc), "ANCHOR_ROLLBACK_UNAVAILABLE")
    tj["learned_anchors"] = la
    repo.update(template_id, {"template_json": tj})
    logger.info("template_anchors_rolled_back", template_id=template_id, version=la["version"])
    return APIResponse(data={"template_id": template_id, "version": la["version"],
                             "field_count": len(la.get("fields", {}))})


@router.post("/{template_id}/unlock-anchors", response_model=APIResponse[dict])
def unlock_template_anchors(template_id: int, db: Session = Depends(get_db)):
    """
    Unlock ground-truth anchors to allow modifying or re-calibrating them.
    """
    repo = TemplateRepository(db)
    template = repo.get_by_id(template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")

    tj = dict(template.template_json or {})
    la = dict(tj.get("learned_anchors") or {})
    la["is_verified"] = False
    tj["learned_anchors"] = la
    template = repo.update(template_id, {"template_json": tj})

    logger.info("template_anchors_unlocked", template_id=template_id)
    return APIResponse(data={
        "message": "Template ground-truth anchors unlocked for modification.",
        "template_id": template_id,
        "is_verified": False,
    })


@router.delete("/{template_id}/learned-anchors", response_model=APIResponse[dict])
def reset_template_anchors(template_id: int, db: Session = Depends(get_db)):
    """
    Reset and purge all stored learned geometric memory from the template.
    """
    repo = TemplateRepository(db)
    template = repo.get_by_id(template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")

    tj = dict(template.template_json or {})
    if "learned_anchors" in tj:
        del tj["learned_anchors"]
    template = repo.update(template_id, {"template_json": tj})

    logger.info("template_anchors_reset", template_id=template_id)
    return APIResponse(data={
        "message": "Template geometric anchor memory has been reset.",
        "template_id": template_id,
    })


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


@router.get("/id/{template_id}", response_model=APIResponse[TemplateResponse])
def get_template_by_id(template_id: int, db: Session = Depends(get_db)):
    """Get a specific template by ID."""
    repo = TemplateRepository(db)
    template = repo.get_by_id(template_id)
    if not template:
        raise DocIntError(f"Template {template_id} not found.", "TEMPLATE_NOT_FOUND")
    return APIResponse(data=template)


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
