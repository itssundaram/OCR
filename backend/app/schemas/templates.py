"""
DOCINT — Template Schemas
"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator
from typing import Literal

class TemplateFieldSchema(BaseModel):
    name: str = Field(..., description="The key name for the field in the output JSON")
    type: Literal["text", "date", "number", "boolean", "table"] = Field(..., description="The data type of the field")
    description: str = Field(..., description="Instructions for the AI on what this field means")
    example: str | None = Field(None, description="An example value")
    required: bool = Field(False, description="Whether this field must be present")
    aliases: list[str] = Field(default_factory=list, description="Alternative labels for this field (e.g. 'Consign To')")
    extraction_hint: str | None = Field(None, description="Optional regex pattern for extraction")
    known_values: list[str] = Field(default_factory=list, description="Allowed values for strict guardrail validation")

class TemplateJsonSchema(BaseModel):
    department: str | None = None
    document_type: str | None = None
    fields: list[TemplateFieldSchema] = Field(..., description="List of fields to extract")


class TemplateBase(BaseModel):
    code: str = Field(..., max_length=100)
    template_json: dict[str, Any] = Field(..., description="The schema for extraction")
    extraction_instructions: str | None = Field(None)

    @field_validator("template_json")
    def validate_template_json(cls, v):
        # Validate against TemplateJsonSchema to ensure it matches our expected structure
        # (raises ValidationError if invalid)
        TemplateJsonSchema.model_validate(v)
        return v


class TemplateCreate(TemplateBase):
    pass

class TemplateUpdate(BaseModel):
    code: str | None = Field(None, max_length=100)
    template_json: dict[str, Any] | None = Field(None, description="The schema for extraction")
    extraction_instructions: str | None = Field(None)

    @field_validator("template_json")
    def validate_template_json(cls, v):
        if v is not None:
            TemplateJsonSchema.model_validate(v)
        return v


class TemplateResponse(TemplateBase):
    id: int
    department_id: int
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
