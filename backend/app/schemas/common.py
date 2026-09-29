"""
DOCINT — Common API Schemas
Standard response envelopes used by all endpoints.
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str = Field(description="Machine-readable error code")
    message: str = Field(description="Human-readable description")
    details: dict[str, Any] | None = Field(default=None)


class APIResponse(BaseModel, Generic[T]):
    """Standard success response. All successful endpoints use this."""
    success: bool = True
    data: T
    meta: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Standard error response. Stack traces are never included."""
    success: bool = False
    error: ErrorDetail


class PaginatedMeta(BaseModel):
    page: int
    per_page: int
    total: int
    total_pages: int


class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = True
    data: list[T]
    meta: PaginatedMeta


def make_error(code: str, message: str, details: dict | None = None) -> ErrorResponse:
    return ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
