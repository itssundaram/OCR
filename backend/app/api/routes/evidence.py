"""
DOCINT — Evidence API (Phase 16 of the implementation plan)

Exposes the Phase 2 FIELD_EXTRACTIONS provenance rows and the Phase 3 asset
store (page images, persisted per-page by app/services/orchestrator.py and
app/services/extractor.py) through a "click a field, see its source" style
endpoint, per the plan's Phase 16 objective.

Honest scope note: none of the three registered pipelines (Qwen, Surya,
legacy OCR — see app/pipelines/*.py) currently attribute an extracted field
to a precise bounding box; each FieldResult.bbox is always None today (this
is called out directly in those files' own comments, e.g.
"Phase 6/16 territory: this pipeline doesn't yet attribute a field to a
specific page"). Wiring real per-field bboxes is pipeline-level inference
work that needs a running model to validate and is explicitly deferred to
the GPU configuration pass. So this endpoint degrades gracefully: if a field
row has a bbox, it serves the exact crop; otherwise it serves the full page
image the value came from, with `precise: false` in the metadata so the
frontend can show "approximate source page" instead of implying an exact
crop that doesn't exist yet.
"""
import io
import os

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from PIL import Image

from app.db.database import get_db
from app.db.models import FieldExtraction, DocumentPage, Asset, ProcessingJob
from app.schemas.common import APIResponse
from app.core.exceptions import DocIntError
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["Evidence"], prefix="/processing")


def _get_field_row(db: Session, job_id: str, field_name: str) -> FieldExtraction:
    """Prefer the final (is_final=1) row for this field — the consensus row in
    comparison mode, or the single pipeline's own row in OCR-page mode —
    falling back to the most recent row if no final row was recorded."""
    stmt = (
        select(FieldExtraction)
        .where(FieldExtraction.job_id == job_id, FieldExtraction.field_name == field_name)
        .order_by(desc(FieldExtraction.is_final), desc(FieldExtraction.id))
    )
    row = db.execute(stmt).scalars().first()
    if not row:
        raise DocIntError(
            f"No extraction found for field '{field_name}' on job {job_id}.", "FIELD_NOT_FOUND",
        )
    return row


def _get_page_asset(db: Session, field_row: FieldExtraction) -> tuple[DocumentPage | None, Asset | None]:
    if not field_row.page_id:
        return None, None
    page = db.get(DocumentPage, field_row.page_id)
    if not page or not page.image_asset_id:
        return page, None
    asset = db.get(Asset, page.image_asset_id)
    return page, asset


@router.get("/{job_id}/evidence/{field_name}", response_model=APIResponse)
def get_field_evidence(job_id: str, field_name: str, db: Session = Depends(get_db)):
    """Metadata for one field's evidence: value, confidence, which pipeline
    produced it, the page it came from, and whether a precise bbox exists."""
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise DocIntError(f"Processing job {job_id} not found.", "JOB_NOT_FOUND")

    field_row = _get_field_row(db, job_id, field_name)
    page, asset = _get_page_asset(db, field_row)

    has_image = asset is not None and os.path.exists(asset.file_path)
    has_bbox = bool(field_row.bbox_json)

    return APIResponse(data={
        "job_id": job_id,
        "field_name": field_row.field_name,
        "value": field_row.field_value,
        "normalized_value": field_row.normalized_value,
        "confidence": field_row.confidence,
        "extraction_method": field_row.extraction_method,
        "fallback_method": field_row.fallback_method,
        "needs_review": field_row.fallback_method == "needs_review",
        "page_number": page.page_number if page else None,
        "bbox": field_row.bbox_json,
        "precise": has_bbox,
        "has_image": has_image,
        "image_url": f"/api/v1/processing/{job_id}/evidence/{field_name}/image" if has_image else None,
    })


@router.get("/{job_id}/evidence/{field_name}/image")
def get_field_evidence_image(job_id: str, field_name: str, db: Session = Depends(get_db)):
    """Raw PNG bytes: the exact field crop when a bbox is recorded, otherwise
    the whole source page image (graceful degradation — see module docstring)."""
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise DocIntError(f"Processing job {job_id} not found.", "JOB_NOT_FOUND")

    field_row = _get_field_row(db, job_id, field_name)
    _, asset = _get_page_asset(db, field_row)

    if not asset or not os.path.exists(asset.file_path):
        raise DocIntError(
            f"No page image is available for field '{field_name}' on job {job_id}.", "EVIDENCE_IMAGE_NOT_FOUND",
        )

    try:
        img = Image.open(asset.file_path)
        img.load()
    except Exception:
        logger.error("evidence_image_open_failed", job_id=job_id, field_name=field_name, exc_info=True)
        raise DocIntError("Stored page image could not be read.", "EVIDENCE_IMAGE_UNREADABLE")

    bbox = field_row.bbox_json
    if bbox and "crop_asset_id" in bbox:
        crop_asset = db.get(Asset, bbox["crop_asset_id"])
        if crop_asset and os.path.exists(crop_asset.file_path):
            try:
                with open(crop_asset.file_path, "rb") as f:
                    return Response(content=f.read(), media_type="image/png")
            except Exception:
                logger.error("evidence_crop_asset_read_failed", job_id=job_id, field_name=field_name, exc_info=True)

    if bbox and all(k in bbox for k in ("x1", "y1", "x2", "y2")):
        try:
            x1, y1, x2, y2 = int(bbox["x1"]), int(bbox["y1"]), int(bbox["x2"]), int(bbox["y2"])
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(img.width, x2), min(img.height, y2)
            if x2 > x1 and y2 > y1:
                img = img.crop((x1, y1, x2, y2))
        except Exception:
            logger.warning("evidence_bbox_crop_failed", job_id=job_id, field_name=field_name, bbox=bbox)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
