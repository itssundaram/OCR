"""
DOCINT - background refinement controls.

  POST /api/v1/extract/jobs/{job_id}/focus            what the viewer is looking at (refine that first)
  GET  /api/v1/extract/jobs/{job_id}/refine           refinement status (for a page reload)
  POST /api/v1/extract/jobs/{job_id}/refine/cancel    stop refining
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.exceptions import DocIntError
from app.db.database import get_db
from app.db.models import ProcessingResult
from app.fast.refine import cancel_refine, set_focus
from app.schemas.common import APIResponse

router = APIRouter(prefix="/extract/jobs", tags=["Refine"])


class FocusRequest(BaseModel):
    table_index: int = Field(ge=1)
    row_from: int = Field(default=1, ge=0)
    row_to: int = Field(default=60, ge=0)
    page: int | None = None


@router.post("/{job_id}/focus", status_code=202)
def post_focus(job_id: str, req: FocusRequest):
    ok = set_focus(job_id, req.model_dump())
    return APIResponse(data={"job_id": job_id, "accepted": ok})


@router.get("/{job_id}/refine")
def get_refine(job_id: str, db: Session = Depends(get_db)):
    row = db.query(ProcessingResult).filter_by(job_id=job_id).first()
    if row is None:
        raise DocIntError(f"No result for job {job_id}.", "JOB_NOT_FOUND")
    return APIResponse(data={"job_id": job_id, "refine": (row.ocr_metadata_json or {}).get("refine")})


@router.post("/{job_id}/refine/cancel", status_code=202)
def post_cancel(job_id: str):
    return APIResponse(data={"job_id": job_id, "requested": cancel_refine(job_id)})


# ── cell close-up (for the review grid) ──────────────────────────────────────
_page_cache: dict[tuple[str, int], object] = {}


def _page_image(path: str, mime: str, page: int):
    key = (path, page)
    if key not in _page_cache:
        if len(_page_cache) >= 6:
            _page_cache.pop(next(iter(_page_cache)))
        from app.fast.triage import DocumentSource
        _page_cache[key] = DocumentSource(path, mime).load_page(page - 1)[0]
    return _page_cache[key]


@router.get("/{job_id}/tables/{table_index}/cells/{row}/{col}/image")
def get_cell_image(job_id: str, table_index: int, row: int, col: int, pad: int = 10, db: Session = Depends(get_db)):
    """PNG close-up of one table cell, cut from the rendered page the extraction used."""
    from fastapi.responses import Response
    import io
    from app.db.models import Document, ProcessingJob

    res = db.query(ProcessingResult).filter_by(job_id=job_id).first()
    job = db.get(ProcessingJob, job_id)
    doc = db.get(Document, job.document_id) if job else None
    if res is None or doc is None:
        raise DocIntError(f"No result for job {job_id}.", "JOB_NOT_FOUND")
    table = next((t for t in (res.extracted_json or {}).get("tables") or [] if t.get("table_index") == table_index), None)
    cell = next((c for c in (table or {}).get("cells") or [] if c.get("row") == row and c.get("col") == col), None)
    if not cell or not cell.get("bbox"):
        raise DocIntError("That cell has no position on the page.", "CELL_NOT_FOUND")
    pad = max(0, min(60, pad))
    img = _page_image(doc.file_path, doc.file_type, int(cell.get("page") or table.get("page_number") or 1))
    b = cell["bbox"]
    box = (max(0, b["x1"] - pad), max(0, b["y1"] - pad), min(img.width, b["x2"] + pad), min(img.height, b["y2"] + pad))
    buf = io.BytesIO()
    img.crop(box).convert("RGB").save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png", headers={"Cache-Control": "private, max-age=300"})
