"""Allow WARNING in DOCUMENTS/PROCESSING_JOBS status check constraints

The app has used "WARNING" (completed, but with fields flagged for review)
as a legitimate job/document status since the orchestrator/consensus work
was added (app/services/extractor.py, app/services/orchestrator.py), but
the original CK_DOC_STATUS / CK_PJ_STATUS check constraints were never
updated to allow it. Every job that legitimately finished with at least one
flagged field was failing on the final `db.commit()` — rolling back all of
that job's results (FieldExtractions, ProcessingResult, consensus rows) and
reporting the whole job as FAILED, even though extraction actually
succeeded. This migration brings the constraints in line with the status
values the application has always written.

Revision ID: b2c3d4e5f6a7
Revises: a74c74bff673
Create Date: 2026-10-06
"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a74c74bff673'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint('ck_doc_status', 'DOCUMENTS', type_='check')
    op.create_check_constraint(
        'ck_doc_status', 'DOCUMENTS',
        "status IN ('PENDING', 'QUEUED', 'PROCESSING', 'COMPLETED', 'WARNING', 'FAILED')",
    )

    op.drop_constraint('ck_pj_status', 'PROCESSING_JOBS', type_='check')
    op.create_check_constraint(
        'ck_pj_status', 'PROCESSING_JOBS',
        "status IN ('QUEUED', 'PROCESSING', 'OCR_PROCESSING', 'AI_PROCESSING', 'VALIDATING', 'COMPLETED', 'WARNING', 'FAILED')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_pj_status', 'PROCESSING_JOBS', type_='check')
    op.create_check_constraint(
        'ck_pj_status', 'PROCESSING_JOBS',
        "status IN ('QUEUED', 'PROCESSING', 'OCR_PROCESSING', 'AI_PROCESSING', 'VALIDATING', 'COMPLETED', 'FAILED')",
    )

    op.drop_constraint('ck_doc_status', 'DOCUMENTS', type_='check')
    op.create_check_constraint(
        'ck_doc_status', 'DOCUMENTS',
        "status IN ('PENDING', 'QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED')",
    )
