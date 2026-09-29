"""
DOCINT — Document Type Schemas
"""
from datetime import datetime

from pydantic import BaseModel, Field


class DocumentTypeBase(BaseModel):
    code: str = Field(..., max_length=50, description="Unique uppercase code (e.g. INVOICE)")
    name: str = Field(..., max_length=200, description="Human-readable name")
    description: str | None = Field(None, max_length=1000)
    department: str = Field(default="GENERAL", max_length=100, description="Department slug (e.g. FINANCE)")


class DocumentTypeCreate(DocumentTypeBase):
    pass


class DocumentTypeUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=1000)
    department: str | None = Field(None, max_length=100)


class DocumentTypeResponse(DocumentTypeBase):
    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
