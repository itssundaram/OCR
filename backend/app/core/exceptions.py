"""
DOCINT — Custom Exception Hierarchy
All application-specific exceptions live here.
HTTP status mapping is handled in main.py exception handlers.
"""
from __future__ import annotations


class DocIntError(Exception):
    """Base class for all DOCINT application errors."""

    def __init__(self, message: str, code: str = "INTERNAL_ERROR", details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


# ── Department Errors ───────────────────────────────────────────────────────────

class DepartmentNotFoundError(DocIntError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"Department '{slug}' does not exist.", "DEPARTMENT_NOT_FOUND")


class DepartmentInactiveError(DocIntError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"Department '{slug}' is inactive.", "DEPARTMENT_INACTIVE")


class DepartmentAlreadyExistsError(DocIntError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"Department with slug '{slug}' already exists.", "DEPARTMENT_ALREADY_EXISTS")


# ── Template Errors ───────────────────────────────────────────────────────────

class TemplateNotFoundError(DocIntError):
    def __init__(self, department_slug: str, template_code: str) -> None:
        super().__init__(f"No active template found for '{department_slug}/{template_code}'.", "TEMPLATE_NOT_FOUND")


class TemplateValidationError(DocIntError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"Template validation failed: {reason}", "TEMPLATE_VALIDATION_ERROR")


class TemplateAlreadyActiveError(DocIntError):
    def __init__(self, department_slug: str, template_code: str, active_version: int) -> None:
        super().__init__(
            f"Template '{department_slug}/{template_code}' already has active version v{active_version}. Deactivate it first.",
            "TEMPLATE_ALREADY_ACTIVE",
            {"active_version": active_version},
        )


# ── Document / File Errors ────────────────────────────────────────────────────

class DocumentNotFoundError(DocIntError):
    def __init__(self, document_id: str) -> None:
        super().__init__(f"Document '{document_id}' not found.", "DOCUMENT_NOT_FOUND")


class FileTypeInvalidError(DocIntError):
    def __init__(self, mime_type: str) -> None:
        super().__init__(f"File type '{mime_type}' is not supported.", "FILE_TYPE_INVALID")


class FileTooLargeError(DocIntError):
    def __init__(self, size_bytes: int, max_bytes: int) -> None:
        super().__init__(
            f"File size {size_bytes / 1024 / 1024:.1f} MB exceeds maximum {max_bytes / 1024 / 1024:.0f} MB.",
            "FILE_TOO_LARGE",
        )


class FileStorageError(DocIntError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"File storage operation failed: {reason}", "FILE_STORAGE_ERROR")


# ── Processing Errors ─────────────────────────────────────────────────────────

class ProcessingJobNotFoundError(DocIntError):
    def __init__(self, job_id: str) -> None:
        super().__init__(f"Processing job '{job_id}' not found.", "JOB_NOT_FOUND")


class ProcessingError(DocIntError):
    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"Processing failed at stage '{stage}': {reason}", "PROCESSING_ERROR", {"stage": stage})


# ── OCR Errors ────────────────────────────────────────────────────────────────

class OCREngineUnavailableError(DocIntError):
    def __init__(self, engine: str) -> None:
        super().__init__(f"OCR engine '{engine}' is not available.", "OCR_ENGINE_UNAVAILABLE")


class OCRProcessingError(DocIntError):
    def __init__(self, engine: str, reason: str) -> None:
        super().__init__(f"OCR processing failed with '{engine}': {reason}", "OCR_PROCESSING_ERROR", {"engine": engine})


# ── AI Errors ─────────────────────────────────────────────────────────────────

class AIEngineUnavailableError(DocIntError):
    def __init__(self, message: str):
        super().__init__(message, "AI_ENGINE_UNAVAILABLE")

class AIProcessingError(DocIntError):
    def __init__(self, engine_name: str, message: str):
        super().__init__(f"AI Engine {engine_name} failed: {message}", "AI_PROCESSING_ERROR")

class AIExtractionError(DocIntError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"AI extraction failed: {reason}", "AI_EXTRACTION_ERROR")


# ── Database Errors ───────────────────────────────────────────────────────────

class DatabaseConnectionError(DocIntError):
    def __init__(self) -> None:
        super().__init__("Cannot connect to Oracle Database. Check ORACLE_* configuration.", "DATABASE_CONNECTION_ERROR")
