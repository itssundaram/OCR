"""add_departments_table_and_department_column

Revision ID: a1b2c3d4e5f6
Revises: 326fefe65ac4
Create Date: 2026-08-27 15:50:00.000000
"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '326fefe65ac4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'DEPARTMENTS',
        sa.Column('id', sa.Integer(), sa.Sequence('departments_id_seq'), nullable=False),
        sa.Column('slug', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.String(length=1000), nullable=True),
        sa.Column('color', sa.String(length=20), nullable=False, server_default='#6366f1'),
        sa.Column('icon', sa.String(length=50), nullable=False, server_default='folder'),
        sa.Column('is_active', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', name='uq_dept_slug'),
        sa.CheckConstraint('is_active IN (0, 1)', name='ck_dept_active'),
    )
    op.create_index('idx_dept_slug', 'DEPARTMENTS', ['slug'], unique=True)

    # Add department column to DOCUMENT_TYPES
    op.add_column(
        'DOCUMENT_TYPES',
        sa.Column('department', sa.String(length=100), nullable=False, server_default='GENERAL')
    )
    op.create_index('idx_doc_type_dept', 'DOCUMENT_TYPES', ['department'])


def downgrade() -> None:
    op.drop_index('idx_doc_type_dept', table_name='DOCUMENT_TYPES')
    op.drop_column('DOCUMENT_TYPES', 'department')
    op.drop_index('idx_dept_slug', table_name='DEPARTMENTS')
    op.drop_table('DEPARTMENTS')
