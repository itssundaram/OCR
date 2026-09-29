"""
DOCINT — Document Management API
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.database import get_db
from app.db.models import Document
from app.schemas.common import APIResponse
from app.schemas.documents import DocumentResponse
from app.core.exceptions import DocIntError

router = APIRouter(tags=["Documents"], prefix="/documents")


@router.get("/{document_id}", response_model=APIResponse[DocumentResponse])
def get_document(document_id: str, db: Session = Depends(get_db)):
    """Get document metadata by ID."""
    doc = db.get(Document, document_id)
    if not doc:
        raise DocIntError(f"Document {document_id} not found.", "DOCUMENT_NOT_FOUND")
    
    return APIResponse(data=doc)


@router.get("/", response_model=APIResponse[list[DocumentResponse]])
def list_documents(
    skip: int = 0, 
    limit: int = 100, 
    template_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db)
):
    """List documents with optional filtering."""
    stmt = select(Document)
    
    if template_id:
        stmt = stmt.where(Document.template_id == template_id)
    if status:
        stmt = stmt.where(Document.status == status.upper())
        
    stmt = stmt.order_by(Document.created_at.desc()).offset(skip).limit(limit)
    docs = db.execute(stmt).scalars().all()
    
    return APIResponse(data=docs)
