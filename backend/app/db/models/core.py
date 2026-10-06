"""
DOCINT — SQLAlchemy Models
Includes all 5 tables:
- DEPARTMENTS
- TEMPLATES
- DOCUMENTS
- PROCESSING_JOBS
- PROCESSING_RESULTS
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Sequence,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base, OracleJSON


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Department(Base):
    __tablename__ = "DEPARTMENTS"

    id: Mapped[int] = mapped_column(
        Integer, Sequence("departments_id_seq"), primary_key=True
    )
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    color: Mapped[str] = mapped_column(String(20), default="#6366f1", nullable=False)
    icon: Mapped[str] = mapped_column(String(50), default="folder", nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("is_active IN (0, 1)", name="ck_dept_active"),
    )

    templates: Mapped[list["Template"]] = relationship(
        "Template", back_populates="department", cascade="all, delete-orphan"
    )


class Template(Base):
    __tablename__ = "TEMPLATES"

    id: Mapped[int] = mapped_column(
        Integer, Sequence("doc_templates_id_seq"), primary_key=True
    )
    department_id: Mapped[int] = mapped_column(
        ForeignKey("DEPARTMENTS.id"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    template_json: Mapped[dict[str, Any]] = mapped_column(OracleJSON, nullable=False)
    extraction_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("department_id", "code", "version", name="uq_tmpl_dept_code_ver"),
        CheckConstraint("is_active IN (0, 1)", name="ck_tmpl_active"),
        Index("idx_tmpl_dept_code", "department_id", "code"),
        Index("idx_tmpl_active_lookup", "department_id", "code", "is_active"),
        # Function-based unique index for active templates
        Index(
            "uq_one_active_tmpl_per_code",
            text("CASE WHEN is_active = 1 THEN department_id ELSE NULL END"),
            text("CASE WHEN is_active = 1 THEN code ELSE NULL END"),
            unique=True,
        ),
    )

    department: Mapped["Department"] = relationship(
        "Department", back_populates="templates"
    )
    jobs: Mapped[list["ProcessingJob"]] = relationship(
        "ProcessingJob", back_populates="template"
    )


class Document(Base):
    __tablename__ = "DOCUMENTS"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(2000), nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("TEMPLATES.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'QUEUED', 'PROCESSING', 'COMPLETED', 'WARNING', 'FAILED')",
            name="ck_doc_status",
        ),
    )

    jobs: Mapped[list["ProcessingJob"]] = relationship(
        "ProcessingJob", back_populates="document", cascade="all, delete-orphan"
    )


class ProcessingJob(Base):
    __tablename__ = "PROCESSING_JOBS"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("DOCUMENTS.id"), nullable=False, index=True
    )
    template_id: Mapped[int] = mapped_column(
        ForeignKey("TEMPLATES.id"), nullable=False, index=True
    )
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    pipeline_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(50), default="QUEUED", nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    ocr_engine: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ocr_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_engine: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'PROCESSING', 'OCR_PROCESSING', 'AI_PROCESSING', 'VALIDATING', 'COMPLETED', 'WARNING', 'FAILED')",
            name="ck_pj_status",
        ),
    )

    document: Mapped["Document"] = relationship("Document", back_populates="jobs")
    template: Mapped["Template"] = relationship("Template", back_populates="jobs")
    result: Mapped["ProcessingResult"] = relationship(
        "ProcessingResult", back_populates="job", uselist=False, cascade="all, delete-orphan"
    )


class ProcessingResult(Base):
    __tablename__ = "PROCESSING_RESULTS"

    id: Mapped[int] = mapped_column(
        Integer, Sequence("proc_results_id_seq"), primary_key=True
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("PROCESSING_JOBS.id"), nullable=False, unique=True, index=True
    )

    extracted_json: Mapped[dict[str, Any]] = mapped_column(OracleJSON, nullable=False)
    confidence_json: Mapped[dict[str, Any]] = mapped_column(OracleJSON, nullable=False)
    ocr_metadata_json: Mapped[dict[str, Any] | None] = mapped_column(OracleJSON, nullable=True)
    validation_warnings: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )

    job: Mapped["ProcessingJob"] = relationship("ProcessingJob", back_populates="result")


class DocumentPage(Base):
    __tablename__ = "DOCUMENT_PAGES"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    document_id: Mapped[str] = mapped_column(ForeignKey("DOCUMENTS.id"), nullable=False, index=True)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )


class Asset(Base):
    __tablename__ = "ASSETS"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_path: Mapped[str] = mapped_column(String(2000), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )


class PipelineRun(Base):
    __tablename__ = "PIPELINE_RUNS"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("PROCESSING_JOBS.id"), nullable=False, index=True)
    pipeline_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="QUEUED")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(OracleJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )


class FieldExtraction(Base):
    __tablename__ = "FIELD_EXTRACTIONS"

    id: Mapped[int] = mapped_column(Integer, Sequence("field_ext_id_seq"), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("PROCESSING_JOBS.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[str | None] = mapped_column(ForeignKey("PIPELINE_RUNS.id"), nullable=True)
    page_id: Mapped[str | None] = mapped_column(ForeignKey("DOCUMENT_PAGES.id"), nullable=True)
    field_name: Mapped[str] = mapped_column(String(500), nullable=False)
    field_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    fallback_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bbox_json: Mapped[dict[str, Any] | None] = mapped_column(OracleJSON, nullable=True)
    crop_asset_id: Mapped[str | None] = mapped_column(ForeignKey("ASSETS.id"), nullable=True)
    is_final: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )


class TableExtraction(Base):
    __tablename__ = "TABLE_EXTRACTIONS"

    id: Mapped[int] = mapped_column(Integer, Sequence("table_ext_id_seq"), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("PROCESSING_JOBS.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[str | None] = mapped_column(ForeignKey("PIPELINE_RUNS.id"), nullable=True)
    page_id: Mapped[str | None] = mapped_column(ForeignKey("DOCUMENT_PAGES.id"), nullable=True)
    table_index: Mapped[int] = mapped_column(Integer, default=0)
    extraction_method: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    bbox_json: Mapped[dict[str, Any] | None] = mapped_column(OracleJSON, nullable=True)
    crop_asset_id: Mapped[str | None] = mapped_column(ForeignKey("ASSETS.id"), nullable=True)
    headers_json: Mapped[list[Any] | None] = mapped_column(OracleJSON, nullable=True)
    rows_json: Mapped[list[Any] | None] = mapped_column(OracleJSON, nullable=True)
    markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )


class ProcessingEvent(Base):
    __tablename__ = "PROCESSING_EVENTS"

    id: Mapped[int] = mapped_column(Integer, Sequence("proc_event_id_seq"), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("PROCESSING_JOBS.id"), nullable=False, index=True)
    pipeline_run_id: Mapped[str | None] = mapped_column(ForeignKey("PIPELINE_RUNS.id"), nullable=True)
    page_id: Mapped[str | None] = mapped_column(ForeignKey("DOCUMENT_PAGES.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(OracleJSON, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False, index=True
    )


class DepartmentUrl(Base):
    __tablename__ = "DEPARTMENT_URLS"

    id: Mapped[int] = mapped_column(Integer, Sequence("dept_urls_id_seq"), primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("DEPARTMENTS.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    label: Mapped[str | None] = mapped_column(String(500), nullable=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("TEMPLATES.id"), nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), onupdate=func.current_timestamp(), nullable=False
    )


class TemplateField(Base):
    __tablename__ = "TEMPLATE_FIELDS"

    id: Mapped[int] = mapped_column(Integer, Sequence("tmpl_fields_id_seq"), primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("TEMPLATES.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(500), nullable=False)
    field_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_required: Mapped[int] = mapped_column(Integer, default=0)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.7)
    validation_regex: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.current_timestamp(), nullable=False
    )

