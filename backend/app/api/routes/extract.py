"""
DOCINT — Department-based Extraction API
Primary extraction endpoints following the pattern: /api/v1/extract/{department_slug}/{template_code}

Three upload flows live here, matching the product's three entry points:
  - POST /{dept}/{template}                    : today's existing production
    flow (DIRECT_LLM_CALL/OCR_STRATEGY-gated, unchanged).
  - POST /{dept}/{template}/pipeline/{name}     : OCR page (Phase 13) — pick
    ONE registered pipeline; only it processes the document.
  - POST /{dept}/{template}/compare             : Orchestration page (Phase 13) —
    pick several pipelines; all run concurrently and are reconciled by
    app/ai/consensus.py.
"""
from __future__ import annotations

import base64
import io
import os
import shutil
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from PIL import Image, ImageOps
import pytesseract
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.exceptions import DocIntError
from app.core.logging import get_logger
from app.core.security import (
    generate_stored_filename,
    safe_file_path,
    sanitize_original_filename,
    validate_file,
)
from app.db.database import get_db
from app.db.models import (
    Asset,
    Department,
    Document,
    DocumentPage,
    FieldExtraction,
    ProcessingJob,
    ProcessingResult,
    Template,
)
from app.ocr.image_extractor import extract_pages_as_images
from app.pipelines.registry import get_pipeline, list_pipelines
from app.schemas.common import APIResponse
from app.services.extractor import process_document_job
from app.services.orchestrator import run_pipeline_comparison_job, run_single_pipeline_job
from app.services.table_edit import apply_cell_edit

logger = get_logger(__name__)

router = APIRouter(tags=["Extraction"])


class ReOcrFieldRequest(BaseModel):
    job_id: str
    field_key: str
    bbox: dict[str, Any]
    page_number: int = 1


class UpdateFieldValueRequest(BaseModel):
    job_id: str
    field_key: str
    value: str
    bbox: dict[str, Any] | None = None
    page_number: int | None = None                       # set when the value now comes from another page
    occurrences: list[dict[str, Any]] | None = None      # the field's other-page readings after that swap


def _load_job_page_image(db: Session, job: ProcessingJob, page_number: int = 1) -> Image.Image:
    """Helper to locate or extract full PIL Image for a given job's page."""
    page_stmt = select(DocumentPage).where(
        DocumentPage.document_id == job.document_id,
        DocumentPage.page_number == page_number,
    )
    dp = db.execute(page_stmt).scalars().first()
    if dp and dp.image_asset_id:
        asset = db.get(Asset, dp.image_asset_id)
        if asset and os.path.exists(asset.file_path):
            img = Image.open(asset.file_path)
            img.load()
            return img.convert("RGB")

    doc = db.get(Document, job.document_id)
    if not doc or not os.path.exists(doc.file_path):
        raise DocIntError("Underlying document file could not be found.", "DOCUMENT_FILE_MISSING")

    images = extract_pages_as_images(doc.file_path, doc.mime_type)
    p_idx = max(0, page_number - 1)
    if p_idx < len(images):
        return images[p_idx].convert("RGB")

    raise DocIntError(f"Page {page_number} not available in document.", "PAGE_NOT_FOUND")


@router.post("/extract/re-ocr-field", response_model=APIResponse[dict])
def re_ocr_field(req: ReOcrFieldRequest, db: Session = Depends(get_db)):
    """
    Sub-50ms Micro-OCR on a user-corrected bounding box.
    Crops the page image, executes fast enhanced OCR, updates job results in DB,
    and returns the new value, confidence, and preview crop image.
    """
    job = db.get(ProcessingJob, req.job_id)
    if not job:
        raise DocIntError(f"Processing job {req.job_id} not found.", "JOB_NOT_FOUND")

    full_img = _load_job_page_image(db, job, req.page_number)

    x1 = max(0, int(round(float(req.bbox.get("x1", 0)))))
    y1 = max(0, int(round(float(req.bbox.get("y1", 0)))))
    x2 = min(full_img.width, int(round(float(req.bbox.get("x2", full_img.width)))))
    y2 = min(full_img.height, int(round(float(req.bbox.get("y2", full_img.height)))))

    if x2 <= x1 or y2 <= y1:
        raise DocIntError("Bounding box coordinates invalid or too small.", "INVALID_BBOX")

    crop = full_img.crop((x1, y1, x2, y2))

    gray = crop.convert("L")
    enhanced = ImageOps.autocontrast(gray)
    if enhanced.height < 40 or enhanced.width < 60:
        enhanced = enhanced.resize((enhanced.width * 2, enhanced.height * 2), Image.Resampling.LANCZOS)

    try:
        data = pytesseract.image_to_data(enhanced, output_type=pytesseract.Output.DICT, config="--psm 6")
        words = []
        confs = []
        for idx in range(len(data.get("text", []))):
            w = data["text"][idx].strip()
            c = float(data["conf"][idx]) if float(data["conf"][idx]) > 0 else 0.0
            if w:
                words.append(w)
                if c > 0:
                    confs.append(c)
        extracted_text = " ".join(words).strip()
        avg_conf = (sum(confs) / len(confs) / 100.0) if confs else (0.85 if extracted_text else 0.0)
    except Exception as e:
        logger.warning("re_ocr_pytesseract_failed", error=str(e))
        extracted_text = ""
        avg_conf = 0.0

    tight_bbox = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

    # 1. Update ProcessingResult
    res_stmt = select(ProcessingResult).where(ProcessingResult.job_id == req.job_id).with_for_update()   # the background refiner writes this row too
    result = db.execute(res_stmt).scalars().first()
    if result:
        ej = dict(result.extracted_json or {})
        fields = dict(ej.get("fields", {}))
        field_entry = dict(fields.get(req.field_key, {}))
        field_entry["value"] = extracted_text
        field_entry["confidence"] = round(avg_conf, 3)
        field_entry["found"] = bool(extracted_text)
        field_entry["bbox"] = tight_bbox
        field_entry["match_strategy"] = "MANUAL_RE_OCR"
        field_entry["is_verified"] = True
        fields[req.field_key] = field_entry
        ej["fields"] = fields
        result.extracted_json = ej
        flag_modified(result, "extracted_json")

    # 2. Update FieldExtraction row
    fe_stmt = select(FieldExtraction).where(
        FieldExtraction.job_id == req.job_id,
        FieldExtraction.field_name == req.field_key,
    )
    fe = db.execute(fe_stmt).scalars().first()
    if fe:
        fe.field_value = extracted_text
        fe.confidence = avg_conf
        fe.bbox_json = tight_bbox
        fe.extraction_method = "manual_re_ocr"
    else:
        new_fe = FieldExtraction(
            job_id=req.job_id,
            field_name=req.field_key,
            field_value=extracted_text,
            confidence=avg_conf,
            bbox_json=tight_bbox,
            extraction_method="manual_re_ocr",
            is_final=1,
        )
        db.add(new_fe)

    db.commit()

    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    b64_crop = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('utf-8')}"

    return APIResponse(data={
        "field_key": req.field_key,
        "value": extracted_text,
        "confidence": round(avg_conf, 3),
        "bbox": tight_bbox,
        "image_data": b64_crop,
    })


@router.post("/extract/update-field", response_model=APIResponse[dict])
def update_field_value(req: UpdateFieldValueRequest, db: Session = Depends(get_db)):
    """
    Save manually edited text and/or bounding box for a field.
    Sets confidence to 100% (human verified ground truth).
    """
    job = db.get(ProcessingJob, req.job_id)
    if not job:
        raise DocIntError(f"Processing job {req.job_id} not found.", "JOB_NOT_FOUND")

    res_stmt = select(ProcessingResult).where(ProcessingResult.job_id == req.job_id).with_for_update()   # the background refiner writes this row too
    result = db.execute(res_stmt).scalars().first()
    if result:
        ej = dict(result.extracted_json or {})
        fields = dict(ej.get("fields", {}))
        field_entry = dict(fields.get(req.field_key, {}))
        field_entry["value"] = req.value
        field_entry["confidence"] = 1.0
        field_entry["found"] = True
        field_entry["manually_edited"] = True
        field_entry["is_verified"] = True
        if req.bbox:
            field_entry["bbox"] = req.bbox
        if req.page_number:
            field_entry["page_number"] = req.page_number
            field_entry["source_page"] = req.page_number
        if req.occurrences is not None:
            field_entry["occurrences"] = req.occurrences
        fields[req.field_key] = field_entry
        ej["fields"] = fields
        result.extracted_json = ej
        flag_modified(result, "extracted_json")

    fe_stmt = select(FieldExtraction).where(
        FieldExtraction.job_id == req.job_id,
        FieldExtraction.field_name == req.field_key,
    )
    fe = db.execute(fe_stmt).scalars().first()
    page_id = None
    if req.page_number:
        pg = db.execute(select(DocumentPage).where(
            DocumentPage.document_id == job.document_id, DocumentPage.page_number == req.page_number,
        )).scalars().first()
        page_id = pg.id if pg else None
    if fe:
        fe.field_value = req.value
        fe.confidence = 1.0
        if req.bbox:
            fe.bbox_json = req.bbox
        if page_id:
            fe.page_id = page_id
    else:
        new_fe = FieldExtraction(
            job_id=req.job_id,
            field_name=req.field_key,
            field_value=req.value,
            confidence=1.0,
            bbox_json=req.bbox or {},
            extraction_method="manual_edit",
            is_final=1,
        )
        db.add(new_fe)

    db.commit()
    return APIResponse(data={
        "field_key": req.field_key,
        "value": req.value,
        "confidence": 1.0,
        "is_verified": True,
        "bbox": req.bbox,
    })


class UpdateTableCellRequest(BaseModel):
    job_id: str
    table_index: int
    row: int
    col: int
    value: str


@router.post("/extract/update-table-cell", response_model=APIResponse[dict])
def update_table_cell(req: UpdateTableCellRequest, db: Session = Depends(get_db)):
    """
    Save one manually corrected table cell back into the job's result.
    The cell becomes confidence 1.0 (human verified); headers, rows and the
    markdown view of the table are rebuilt from the cells.
    """
    job = db.get(ProcessingJob, req.job_id)
    if not job:
        raise DocIntError(f"Processing job {req.job_id} not found.", "JOB_NOT_FOUND")
    if len(req.value) > 2000:
        raise DocIntError("Cell text is too long (limit 2000 characters).", "INVALID_CELL")

    res_stmt = select(ProcessingResult).where(ProcessingResult.job_id == req.job_id).with_for_update()   # the background refiner writes this row too
    result = db.execute(res_stmt).scalars().first()
    if not result:
        raise DocIntError("This job has no extraction result yet.", "TABLE_NOT_FOUND")

    ej = dict(result.extracted_json or {})
    tables = list(ej.get("tables") or [])
    pos = next(
        (i for i, t in enumerate(tables) if isinstance(t, dict) and t.get("table_index") == req.table_index),
        None,
    )
    if pos is None:
        raise DocIntError(f"Table {req.table_index} not found in this result.", "TABLE_NOT_FOUND")

    try:
        updated = apply_cell_edit(tables[pos], req.row, req.col, req.value)
    except ValueError as exc:
        raise DocIntError(str(exc), "INVALID_CELL")

    tables[pos] = updated
    ej["tables"] = tables
    result.extracted_json = ej
    flag_modified(result, "extracted_json")
    db.commit()
    return APIResponse(data={"table_index": req.table_index, "table": updated})


class ManageTemplateFieldsRequest(BaseModel):
    job_id: str
    action: str  # "add", "remove", "update"
    field_key: str
    value: str | None = None
    bbox: dict[str, Any] | None = None
    page_number: int = 1
    save_to_template: bool = True


@router.post("/extract/manage-template-field", response_model=APIResponse[dict])
def manage_template_field(req: ManageTemplateFieldsRequest, db: Session = Depends(get_db)):
    """
    Add, remove, or update a field in the current job result, and optionally persist
    it directly to the template's learned_anchors so future runs automatically extract it.
    """
    job = db.get(ProcessingJob, req.job_id)
    if not job:
        raise DocIntError(f"Processing job {req.job_id} not found.", "JOB_NOT_FOUND")

    template = db.get(Template, job.template_id) if job.template_id else None

    # 1. Update ProcessingResult.extracted_json
    res_stmt = select(ProcessingResult).where(ProcessingResult.job_id == req.job_id).with_for_update()   # the background refiner writes this row too
    result = db.execute(res_stmt).scalars().first()
    if result:
        ej = dict(result.extracted_json or {})
        fields = dict(ej.get("fields", {}))

        if req.action == "remove":
            if req.field_key in fields:
                del fields[req.field_key]
        else:  # "add" or "update"
            fields[req.field_key] = {
                "value": req.value or "",
                "confidence": 1.0,
                "found": bool(req.value),
                "bbox": req.bbox,
                "source_page": req.page_number,
                "manually_managed": True,
            }

        ej["fields"] = fields
        result.extracted_json = ej
        flag_modified(result, "extracted_json")

    # 2. Update FieldExtraction DB table
    if req.action == "remove":
        db.query(FieldExtraction).filter_by(job_id=req.job_id, field_name=req.field_key).delete()
    else:
        fe = db.query(FieldExtraction).filter_by(job_id=req.job_id, field_name=req.field_key).first()
        if fe:
            fe.field_value = req.value or ""
            fe.confidence = 1.0
            if req.bbox:
                fe.bbox_json = req.bbox
        else:
            db.add(FieldExtraction(
                job_id=req.job_id,
                field_name=req.field_key,
                field_value=req.value or "",
                confidence=1.0,
                bbox_json=req.bbox or {},
                extraction_method="user_defined",
                is_final=1,
            ))

    # 3. Persist to Template.template_json (learned_anchors) - same merge-safe,
    #    versioned store that lock-anchors uses.
    if req.save_to_template and template:
        from app.anchors import merge_anchor_fields, remove_anchor_field
        from app.api.routes.templates import job_words_by_page

        tj = dict(template.template_json or {})
        la_prev = dict(tj.get("learned_anchors") or {})
        if req.action == "remove":
            la = remove_anchor_field(la_prev, req.field_key)
        elif req.bbox and req.bbox.get("x1") is not None:
            doc_meta = {}
            if result is not None:
                doc_meta = dict(result.ocr_metadata_json or {})
            la, _report = merge_anchor_fields(
                la_prev,
                {req.field_key: {"bbox": req.bbox, "page_number": req.page_number, "value": req.value}},
                page_w=doc_meta.get("doc_page_width") or la_prev.get("page_width") or 1654,
                page_h=doc_meta.get("doc_page_height") or la_prev.get("page_height") or 2338,
                verified=None,  # a single-field edit does not change the lock state
                words_by_page=job_words_by_page(db, req.job_id),
                source="user_field",
            )
        else:
            la = la_prev  # no geometry -> nothing to learn

        tj["learned_anchors"] = la
        template.template_json = tj
        flag_modified(template, "template_json")

    db.commit()

    return APIResponse(data={
        "status": "success",
        "action": req.action,
        "field_key": req.field_key,
        "value": req.value,
        "bbox": req.bbox,
        "template_updated": bool(req.save_to_template and template),
    })



class RerunJobRequest(BaseModel):
    force_relearn: bool = False


@router.post("/extract/jobs/{job_id}/rerun", status_code=202)
def rerun_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    req: RerunJobRequest | None = None,
    db: Session = Depends(get_db),
):
    """
    Process the SAME document again with the template as it is NOW (saved/locked
    anchors included), using the same pipeline as the original job. Creates a new
    job + document row that point at the already-stored file, so nothing is
    uploaded again and the original result stays untouched for comparison.
    """
    req = req or RerunJobRequest()
    old = db.get(ProcessingJob, job_id)
    if not old:
        raise DocIntError(f"Processing job {job_id} not found.", "JOB_NOT_FOUND")
    doc = db.get(Document, old.document_id) if old.document_id else None
    if not doc or not doc.file_path or not os.path.exists(doc.file_path):
        raise DocIntError("The original file is no longer available; upload it again.", "FILE_NOT_FOUND")
    template = db.get(Template, old.template_id) if old.template_id else None
    if not template:
        raise DocIntError("The template for this job no longer exists.", "TEMPLATE_NOT_FOUND")

    mode = old.pipeline_mode or "single_pipeline"
    if mode == "orchestration":
        raise DocIntError("Comparison (multi-pipeline) jobs cannot be re-run from here; upload again to compare.",
                          "RERUN_UNSUPPORTED")
    if mode not in ("single_pipeline",) and mode not in list_pipelines():
        raise DocIntError(f"Pipeline '{mode}' is no longer available.", "PIPELINE_NOT_FOUND")

    try:                                   # the old result is being replaced: stop its background refinement
        from app.fast.refine import cancel_refine
        cancel_refine(job_id)
    except Exception:
        pass
    new_doc_id, new_job_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.add(Document(
        id=new_doc_id,
        original_filename=doc.original_filename,
        stored_filename=doc.stored_filename,
        file_path=doc.file_path,
        file_type=doc.file_type,
        file_size_bytes=doc.file_size_bytes,
        template_id=template.id,
        status="QUEUED",
    ))
    db.add(ProcessingJob(
        id=new_job_id,
        document_id=new_doc_id,
        template_id=template.id,
        template_version=template.version,
        status="QUEUED",
        pipeline_mode=mode,
    ))
    db.commit()

    if mode == "fast_ocr":
        from app.fast.dispatch import dispatch_fast_job
        dispatched_via = dispatch_fast_job(new_job_id, req.force_relearn, background_tasks)
    elif mode == "single_pipeline":
        dispatched_via = _dispatch_job(
            "app.workers.tasks.process_document_job", new_job_id, (), background_tasks, process_document_job,
        )
    else:
        dispatched_via = _dispatch_job(
            "app.workers.tasks.run_single_pipeline_job", new_job_id, (mode, req.force_relearn),
            background_tasks, run_single_pipeline_job,
        )

    logger.info("job_rerun_queued", old_job_id=job_id, new_job_id=new_job_id, pipeline=mode,
                force_relearn=req.force_relearn)
    return APIResponse(data={
        "job_id": new_job_id,
        "document_id": new_doc_id,
        "previous_job_id": job_id,
        "status": "QUEUED",
        "pipeline": mode,
        "dispatched_via": dispatched_via,
        **({"events_url": f"/api/v1/stream/jobs/{new_job_id}"} if mode == "fast_ocr" else {}),
    })


class PromoteFieldRequest(BaseModel):
    field_key: str                       # the key of the "found on document" field in this job's result
    name: str | None = None              # the template field name to use (default: the key)
    field_type: str | None = None        # text | date | number | boolean (default: guessed from the value)


@router.post("/extract/jobs/{job_id}/promote-field")
def promote_found_field(job_id: str, req: PromoteFieldRequest, db: Session = Depends(get_db)):
    """Add a field that was found on the document to the job's template, for every later document. Additive and
    logged (see app/services/template_fields.py); this job's result is updated too, so its export already has the
    field among the template fields. Refused when the name or the printed label clashes with an existing field."""
    from app.services.template_fields import add_field, slug_name
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise DocIntError(f"Processing job {job_id} not found.", "JOB_NOT_FOUND")
    template = db.get(Template, job.template_id) if job.template_id else None
    if template is None:
        raise DocIntError("This job has no template.", "TEMPLATE_NOT_FOUND")
    result = db.execute(select(ProcessingResult).where(ProcessingResult.job_id == job_id).with_for_update()).scalars().first()
    ej = dict(result.extracted_json or {}) if result else {}
    fields = dict(ej.get("fields") or {})
    entry = fields.get(req.field_key)
    if not isinstance(entry, dict) or entry.get("source") != "discovered":
        raise DocIntError("Only a field found on the document can be added to the template.", "NOT_A_FOUND_FIELD")
    name = slug_name(req.name or req.field_key)
    if name != req.field_key and name in fields:
        raise DocIntError(f"This result already has a field named '{name}'.", "FIELD_ALREADY_IN_TEMPLATE")

    tj, fdef = add_field(
        template.template_json or {}, name=name, label=str(entry.get("label") or req.field_key),
        value=str(entry.get("value") or ""), field_type=req.field_type, job_id=job_id,
    )
    template.template_json = tj
    flag_modified(template, "template_json")

    fields.pop(req.field_key, None)
    entry = {**entry, "source": "", "promoted_to_template": True}
    entry.pop("discovered", None)
    fields[name] = entry
    ej["fields"] = fields
    result.extracted_json = ej
    flag_modified(result, "extracted_json")
    if name != req.field_key:
        db.query(FieldExtraction).filter_by(job_id=job_id, field_name=req.field_key).update({"field_name": name})
    db.commit()
    logger.info("template_field_added", template_id=template.id, field=name, job_id=job_id)
    return APIResponse(data={
        "field_name": name, "template_id": template.id, "template_version": template.version,
        "field": fdef, "replaces_key": req.field_key,
    })


@router.get("/extract/jobs/{job_id}/export")
def export_job_json(job_id: str, detail: bool = False, db: Session = Depends(get_db)):
    """The finished result as department-ready JSON: template fields as flat key -> value, other fields found on
    the document separately, and every table as keyed rows. Always built from the stored result, so corrections
    made on the review screen are included. `detail=true` adds confidence / verified / page per field."""
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise DocIntError(f"Processing job {job_id} not found.", "JOB_NOT_FOUND")
    result = db.execute(select(ProcessingResult).where(ProcessingResult.job_id == job_id)).scalars().first()
    if not result:
        raise DocIntError("This job has no extraction result yet.", "RESULT_NOT_READY")
    from app.services.export_json import build_export
    return APIResponse(data={"status": job.status, **build_export(result.extracted_json, job_id=job_id, detail=detail)})


@router.post("/extract/jobs/{job_id}/commit")
def commit_prefetched_job(job_id: str, db: Session = Depends(get_db)):
    """"Execute extraction" on a document that was prefetched: the hidden job becomes an ordinary one (it shows in
    Recent Ingestion Jobs) and keeps whatever the worker has already done. 404-style PREFETCH_GONE when the
    prefetch was discarded or reaped: the page then uploads the document again."""
    from app.services import prefetch
    job = prefetch.commit(db, job_id)
    tmpl = db.get(Template, job.template_id)
    dept = db.get(Department, tmpl.department_id) if tmpl else None
    base_url = f"/api/v1/{(dept.slug if dept else '').lower()}/{(tmpl.code if tmpl else '').lower()}"
    return APIResponse(data={
        "job_id": job.id, "document_id": job.document_id, "status": job.status, "pipeline": job.pipeline_mode,
        "status_url": f"{base_url}/status/{job.id}",
        **({"events_url": f"/api/v1/stream/jobs/{job.id}"} if job.pipeline_mode == "fast_ocr" else {}),
    })


@router.post("/extract/jobs/{job_id}/discard")
def discard_prefetched_job(job_id: str, db: Session = Depends(get_db)):
    """The person picked another document / template / pipeline before pressing Execute: stop and remove the
    hidden job. Refused for a job that was already committed, so a late call cannot cancel real work."""
    from app.services import prefetch
    return APIResponse(data={"job_id": job_id, "result": prefetch.discard(db, job_id)})


def _resolve_department_and_template(db: Session, department_slug: str, template_code: str) -> Template:
    dept_stmt = select(Department).where(Department.slug == department_slug.upper())
    dept = db.execute(dept_stmt).scalars().first()
    if not dept:
        raise DocIntError(f"Department '{department_slug}' not found.", "DEPT_NOT_FOUND")

    tmpl_stmt = select(Template).where(
        Template.department_id == dept.id,
        Template.code == template_code.lower(),
        Template.is_active == 1,
    )
    template = db.execute(tmpl_stmt).scalars().first()
    if not template:
        raise DocIntError(
            f"No active template found for {department_slug}/{template_code}.",
            "TEMPLATE_NOT_FOUND",
        )
    return template


def _save_uploaded_file_and_create_job(db: Session, file: UploadFile, template: Template, pipeline_mode: str = "single_pipeline",
                                       prefetch: bool = False) -> tuple[str, str]:
    """Shared upload mechanics for all three flows above: validate, store the
    file, create the Document + ProcessingJob rows. Returns (doc_id, job_id);
    caller commits and dispatches."""
    header_bytes = file.file.read(2048)
    file.file.seek(0)
    file_size = getattr(file, "size", 0)
    if not file_size:
        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)

    sanitized_filename = sanitize_original_filename(file.filename or "unnamed")
    mime_type = validate_file(header_bytes, sanitized_filename, file_size)

    stored_filename = generate_stored_filename(mime_type)
    file_path = safe_file_path(settings.storage_path, stored_filename)
    try:
        with open(file_path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)
    except Exception as e:
        raise DocIntError(f"Failed to save file: {str(e)}", "FILE_STORAGE_ERROR")

    doc_id = str(uuid.uuid4())
    new_doc = Document(
        id=doc_id,
        original_filename=sanitized_filename,
        stored_filename=stored_filename,
        file_path=str(file_path),
        file_type=mime_type,
        file_size_bytes=file_size,
        template_id=template.id,
        status="QUEUED",
    )
    db.add(new_doc)

    job_id = str(uuid.uuid4())
    new_job = ProcessingJob(
        id=job_id,
        document_id=doc_id,
        template_id=template.id,
        template_version=template.version,
        status="QUEUED",
        pipeline_mode=pipeline_mode,
    )
    if prefetch:
        from app.services.prefetch import mark
        mark(new_job)                                     # hidden from every list until "Execute" commits it
    db.add(new_job)
    db.commit()
    db.refresh(new_doc)
    return doc_id, job_id


def _dispatch_job(
    task_path: str,
    job_id: str,
    task_args: tuple,
    background_tasks: BackgroundTasks,
    fallback_fn,
) -> str:
    """Enqueue `task_path` (a dotted import string, RQ-style) to Redis with
    Phase 10's bounded retry + on_failure policy, falling back to
    BackgroundTasks if Redis is unavailable or the installed rq version
    rejects the retry/callback kwargs. Returns "redis" or "background_task" —
    same contract the original single-flow dispatch block had, now shared by
    all three upload flows."""
    try:
        from app.workers.queue import q as redis_queue
        if redis_queue is not None:
            try:
                from rq import Retry, Callback
                redis_queue.enqueue(
                    task_path,
                    job_id,
                    *task_args,
                    job_id=job_id,  # Sets the Redis Job ID to match our DB job_id
                    job_timeout=settings.JOB_TIMEOUT_SECONDS,
                    retry=Retry(max=settings.JOB_MAX_RETRIES, interval=settings.JOB_RETRY_INTERVAL_SECONDS),
                    on_failure=Callback("app.workers.tasks.job_failed_callback"),
                )
            except TypeError:
                logger.warning("rq_phase10_kwargs_unsupported_falling_back")
                redis_queue.enqueue(
                    task_path, job_id, *task_args,
                    job_id=job_id, job_timeout=settings.JOB_TIMEOUT_SECONDS,
                )
        else:
            raise RuntimeError("Redis queue not initialised")
        return "redis"
    except Exception as redis_err:
        logger.warning("redis_dispatch_failed_fallback", error=str(redis_err), fallback="background_tasks")
        background_tasks.add_task(fallback_fn, job_id, *task_args)
        return "background_task"


@router.post("/{department_slug}/{template_code}", status_code=202)
def extract_document(
    department_slug: str,
    template_code: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload a document for extraction under the given department and template.
    Returns a job_id you can poll at the status endpoint. Unchanged from
    before Phase 13: this is still DOCINT's existing production flow
    (DIRECT_LLM_CALL/OCR_STRATEGY-gated).
    """
    template = _resolve_department_and_template(db, department_slug, template_code)
    doc_id, job_id = _save_uploaded_file_and_create_job(db, file, template)

    dispatched_via = _dispatch_job(
        "app.workers.tasks.process_document_job", job_id, (), background_tasks, process_document_job,
    )

    base_url = f"/api/v1/{department_slug.lower()}/{template_code.lower()}"
    return APIResponse(data={
        "job_id": job_id,
        "document_id": doc_id,
        "status": "QUEUED",
        "dispatched_via": dispatched_via,
        "status_url": f"{base_url}/status/{job_id}",
    })


@router.post("/{department_slug}/{template_code}/pipeline/{pipeline_name}", status_code=202)
def extract_document_with_pipeline(
    department_slug: str,
    template_code: str,
    pipeline_name: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    force_relearn: bool = Form(False),
    prefetch: bool = Form(False),
    db: Session = Depends(get_db),
):
    """
    OCR page (Phase 13): upload with ONE explicitly chosen pipeline. Only
    that pipeline processes the document — see
    app/services/orchestrator.py::run_single_pipeline_job.

    force_relearn: set from the upload page's "this is a new format" checkbox.
    Pipelines that keep a learned geometric pattern per template (currently
    only learned_anchor) use it to skip their stored pattern and relearn from
    this document instead of trusting possibly-stale/mismatched geometry.
    Pipelines that don't have such a concept simply ignore it.
    """
    if pipeline_name not in list_pipelines():
        raise DocIntError(
            f"Unknown pipeline '{pipeline_name}'. Available: {', '.join(list_pipelines())}",
            "PIPELINE_NOT_FOUND",
        )

    t_req = time.perf_counter()
    prefetch = bool(prefetch and settings.PREFETCH_ENABLED)
    template = _resolve_department_and_template(db, department_slug, template_code)
    doc_id, job_id = _save_uploaded_file_and_create_job(db, file, template, pipeline_mode=pipeline_name, prefetch=prefetch)
    t_saved = time.perf_counter()

    if pipeline_name == "fast_ocr":
        # Own runner + queue + live events (app/fast/); every other pipeline
        # keeps the dispatch below, unchanged.
        from app.fast.dispatch import dispatch_fast_job
        dispatched_via = dispatch_fast_job(job_id, force_relearn, background_tasks)
    else:
        dispatched_via = _dispatch_job(
            "app.workers.tasks.run_single_pipeline_job", job_id, (pipeline_name, force_relearn),
            background_tasks, run_single_pipeline_job,
        )

    logger.info("upload_accepted", job_id=job_id, pipeline=pipeline_name, via=dispatched_via,
                save_ms=int((t_saved - t_req) * 1000), dispatch_ms=int((time.perf_counter() - t_saved) * 1000))
    base_url = f"/api/v1/{department_slug.lower()}/{template_code.lower()}"
    return APIResponse(data={
        "job_id": job_id,
        "document_id": doc_id,
        "status": "QUEUED",
        "pipeline": pipeline_name,
        "dispatched_via": dispatched_via,
        "prefetch": prefetch,
        "status_url": f"{base_url}/status/{job_id}",
        **({"events_url": f"/api/v1/stream/jobs/{job_id}"} if pipeline_name == "fast_ocr" else {}),
    })


@router.post("/{department_slug}/{template_code}/compare", status_code=202)
def extract_document_with_comparison(
    department_slug: str,
    template_code: str,
    background_tasks: BackgroundTasks,
    pipelines: list[str] = Query(..., description="Pipeline names to run and compare, e.g. ?pipelines=surya&pipelines=tesseract"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Orchestration page (Phase 13): upload with SEVERAL pipelines selected
    together. All of them run on the same document (concurrently) and their
    field values are reconciled — see
    app/services/orchestrator.py::run_pipeline_comparison_job and
    app/ai/consensus.py.
    """
    available = set(list_pipelines())
    unknown = [p for p in pipelines if p not in available]
    if unknown:
        raise DocIntError(
            f"Unknown pipeline(s): {', '.join(unknown)}. Available: {', '.join(sorted(available))}",
            "PIPELINE_NOT_FOUND",
        )
    if len(pipelines) < 2:
        raise DocIntError("Select at least two pipelines to compare.", "INSUFFICIENT_PIPELINES")

    template = _resolve_department_and_template(db, department_slug, template_code)
    doc_id, job_id = _save_uploaded_file_and_create_job(db, file, template, pipeline_mode="orchestration")

    dispatched_via = _dispatch_job(
        "app.workers.tasks.run_pipeline_comparison_job", job_id, (pipelines,),
        background_tasks, run_pipeline_comparison_job,
    )

    base_url = f"/api/v1/{department_slug.lower()}/{template_code.lower()}"
    return APIResponse(data={
        "job_id": job_id,
        "document_id": doc_id,
        "status": "QUEUED",
        "pipelines": pipelines,
        "dispatched_via": dispatched_via,
        "status_url": f"{base_url}/status/{job_id}",
    })


@router.get("/{department_slug}/{template_code}/status/{job_id}")
def get_extraction_status(
    department_slug: str,
    template_code: str,
    job_id: str,
    db: Session = Depends(get_db),
):
    """
    Poll the status of an extraction job.
    When COMPLETED, returns the full structured extraction result.
    """
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise DocIntError(f"Job {job_id} not found.", "JOB_NOT_FOUND")

    response_data: dict = {
        "job_id": job.id,
        "document_id": job.document_id,
        "template_id": job.template_id,
        "status": job.status,
        "department": department_slug,
        "template": template_code,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "error_message": job.error_message,
        "overall_confidence": job.overall_confidence,
        "ocr_engine": job.ocr_engine,
        "ai_engine": job.ai_engine,
    }

    # Phase 10 introduced "WARNING" as a real completion state (a document
    # that finished but has fields needing review) — it must still surface
    # its result, not be treated like a still-processing job.
    if job.status in ("COMPLETED", "WARNING"):
        if job.document_id:
            stmt_pages = select(DocumentPage).where(DocumentPage.document_id == job.document_id).order_by(DocumentPage.page_number)
            pages = db.execute(stmt_pages).scalars().all()
            if pages:
                response_data["doc_page_width"] = pages[0].width
                response_data["doc_page_height"] = pages[0].height

        result_stmt = select(ProcessingResult).where(ProcessingResult.job_id == job_id)
        result = db.execute(result_stmt).scalars().first()
        if result:
            response_data["result"] = result.extracted_json
            response_data["ocr_metadata_json"] = result.ocr_metadata_json
            response_data["confidence_json"] = result.confidence_json
            # the same data in the shape a department system binds to (computed now, so corrections are in it)
            from app.services.export_json import build_export
            response_data["export"] = build_export(result.extracted_json, job_id=job.id)

    return APIResponse(data=response_data)


@router.get("/{department_slug}/{template_code}/endpoints")
def get_department_endpoints(
    department_slug: str,
    template_code: str,
):
    """
    List all API endpoints available for this specific department and template.
    """
    base_url = f"http://localhost:8000/api/v1/{department_slug.lower()}/{template_code.lower()}"
    return APIResponse(data={
        "extract": {
            "method": "POST",
            "url": f"{base_url}",
            "description": "Upload document for extraction."
        },
        "status": {
            "method": "GET",
            "url": f"{base_url}/status/{{job_id}}",
            "description": "Poll extraction status."
        },
        "endpoints": {
            "method": "GET",
            "url": f"{base_url}/endpoints",
            "description": "List all endpoints."
        }
    })

