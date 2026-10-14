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


def _normalize_template_json(v: Any) -> Any:
    if isinstance(v, dict):
        if "fields" not in v and "properties" in v:
            props = v.get("properties", {})
            req_list = v.get("required", [])
            fields = []
            for prop_name, prop_data in props.items():
                if not isinstance(prop_data, dict):
                    continue
                p_type = prop_data.get("type", "string")
                field_type = "text"
                if p_type in ("number", "integer"):
                    field_type = "number"
                elif p_type == "boolean":
                    field_type = "boolean"
                elif p_type == "array":
                    field_type = "table"
                elif "date" in prop_name.lower():
                    field_type = "date"
                fields.append({
                    "name": prop_name,
                    "type": field_type,
                    "description": prop_data.get("description", "") or "",
                    "required": prop_name in req_list,
                    "example": prop_data.get("example"),
                    "aliases": prop_data.get("aliases", []),
                    "extraction_hint": prop_data.get("pattern") or prop_data.get("extraction_hint"),
                    "known_values": prop_data.get("enum") or prop_data.get("known_values", []),
                })
            v = {**v, "fields": fields}
        try:
            TemplateJsonSchema.model_validate(v)
        except Exception:
            if "fields" not in v:
                v["fields"] = []
    return v


class TemplateBase(BaseModel):
    code: str = Field(..., max_length=100)
    template_json: dict[str, Any] = Field(..., description="The schema for extraction")
    extraction_instructions: str | None = Field(None)

    @field_validator("template_json")
    def validate_template_json(cls, v):
        return _normalize_template_json(v)


class TemplateCreate(TemplateBase):
    pass

class TemplateUpdate(BaseModel):
    code: str | None = Field(None, max_length=100)
    template_json: dict[str, Any] | None = Field(None, description="The schema for extraction")
    extraction_instructions: str | None = Field(None)

    @field_validator("template_json")
    def validate_template_json(cls, v):
        if v is not None:
            return _normalize_template_json(v)
        return v


class TemplateResponse(TemplateBase):
    id: int
    department_id: int
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
