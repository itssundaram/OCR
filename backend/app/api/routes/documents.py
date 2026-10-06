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
from app.repositories.document_repo import DocumentRepository

router = APIRouter(tags=["Documents"], prefix="/documents")


@router.get("/{document_id}", response_model=APIResponse[DocumentResponse])
def get_document(document_id: str, db: Session = Depends(get_db)):
    """Get document metadata by ID."""
    repo = DocumentRepository(db)
    doc = repo.get_by_id(document_id)
    if not doc:
        raise DocIntError(f"Document {document_id} not found.", "DOCUMENT_NOT_FOUND")
    
    return APIResponse(data=doc)

from fastapi.responses import FileResponse

@router.get("/{document_id}/file")
def get_document_file(document_id: str, db: Session = Depends(get_db)):
    """Serve the raw document file (e.g., for PDF preview)."""
    repo = DocumentRepository(db)
    doc = repo.get_by_id(document_id)
    if not doc:
        raise DocIntError(f"Document {document_id} not found.", "DOCUMENT_NOT_FOUND")
    
    import os
    if not os.path.exists(doc.file_path):
        raise DocIntError("File not found on disk.", "FILE_NOT_FOUND")
        
    return FileResponse(
        path=doc.file_path,
        media_type=doc.file_type,
        filename=doc.original_filename,
        content_disposition_type="inline"
    )


@router.get("/", response_model=APIResponse[list[DocumentResponse]])
def list_documents(
    skip: int = 0, 
    limit: int = 100, 
    template_id: int | None = None,
    status: str | None = None,
    db: Session = Depends(get_db)
):
    """List documents with optional filtering."""
    repo = DocumentRepository(db)
    filters = {}
    if template_id:
        filters["template_id"] = template_id
    if status:
        filters["status"] = status.upper()
        
    docs = repo.list_all(filters=filters, skip=skip, limit=limit)
    
    return APIResponse(data=docs)
