"""
DOCINT — Document Schemas
"""
from datetime import datetime
from pydantic import BaseModel, Field

class DocumentResponse(BaseModel):
    id: str = Field(..., description="UUID")
    original_filename: str
    file_type: str
    file_size_bytes: int
    template_id: int | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
