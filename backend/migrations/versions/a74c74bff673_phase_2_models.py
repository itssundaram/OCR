"""Phase 2 Models

Revision ID: a74c74bff673
Revises: a1b2c3d4e5f6
Create Date: 2026-10-06 00:28:39.652962
"""
from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'a74c74bff673'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.db.database import OracleJSON
    
    # Add columns
    op.add_column('DOCUMENTS', sa.Column('page_count', sa.Integer(), nullable=True))
    op.add_column('PROCESSING_JOBS', sa.Column('pipeline_mode', sa.String(length=50), nullable=True))
    op.add_column('PROCESSING_JOBS', sa.Column('page_count', sa.Integer(), nullable=True))
    
    # ASSETS
    op.create_table('ASSETS',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('asset_type', sa.String(length=50), nullable=False),
        sa.Column('file_path', sa.String(length=2000), nullable=False),
        sa.Column('mime_type', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # DOCUMENT_PAGES
    op.create_table('DOCUMENT_PAGES',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('image_asset_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['DOCUMENTS.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # PIPELINE_RUNS
    op.create_table('PIPELINE_RUNS',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('job_id', sa.String(length=36), nullable=False),
        sa.Column('pipeline_name', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='QUEUED'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('overall_confidence', sa.Float(), nullable=True),
        sa.Column('metadata_json', OracleJSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['PROCESSING_JOBS.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # FIELD_EXTRACTIONS
    op.execute(sa.schema.CreateSequence(sa.Sequence('field_ext_id_seq')))
    op.create_table('FIELD_EXTRACTIONS',
        sa.Column('id', sa.Integer(), sa.Sequence('field_ext_id_seq'), primary_key=True),
        sa.Column('job_id', sa.String(length=36), nullable=False),
        sa.Column('pipeline_run_id', sa.String(length=36), nullable=True),
        sa.Column('page_id', sa.String(length=36), nullable=True),
        sa.Column('field_name', sa.String(length=500), nullable=False),
        sa.Column('field_value', sa.Text(), nullable=True),
        sa.Column('normalized_value', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('extraction_method', sa.String(length=100), nullable=True),
        sa.Column('fallback_method', sa.String(length=100), nullable=True),
        sa.Column('bbox_json', OracleJSON(), nullable=True),
        sa.Column('crop_asset_id', sa.String(length=36), nullable=True),
        sa.Column('is_final', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['crop_asset_id'], ['ASSETS.id'], ),
        sa.ForeignKeyConstraint(['job_id'], ['PROCESSING_JOBS.id'], ),
        sa.ForeignKeyConstraint(['page_id'], ['DOCUMENT_PAGES.id'], ),
        sa.ForeignKeyConstraint(['pipeline_run_id'], ['PIPELINE_RUNS.id'], )
    )
    
    # TABLE_EXTRACTIONS
    op.execute(sa.schema.CreateSequence(sa.Sequence('table_ext_id_seq')))
    op.create_table('TABLE_EXTRACTIONS',
        sa.Column('id', sa.Integer(), sa.Sequence('table_ext_id_seq'), primary_key=True),
        sa.Column('job_id', sa.String(length=36), nullable=False),
        sa.Column('pipeline_run_id', sa.String(length=36), nullable=True),
        sa.Column('page_id', sa.String(length=36), nullable=True),
        sa.Column('table_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('extraction_method', sa.String(length=100), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('bbox_json', OracleJSON(), nullable=True),
        sa.Column('crop_asset_id', sa.String(length=36), nullable=True),
        sa.Column('headers_json', OracleJSON(), nullable=True),
        sa.Column('rows_json', OracleJSON(), nullable=True),
        sa.Column('markdown', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['crop_asset_id'], ['ASSETS.id'], ),
        sa.ForeignKeyConstraint(['job_id'], ['PROCESSING_JOBS.id'], ),
        sa.ForeignKeyConstraint(['page_id'], ['DOCUMENT_PAGES.id'], ),
        sa.ForeignKeyConstraint(['pipeline_run_id'], ['PIPELINE_RUNS.id'], )
    )

    # PROCESSING_EVENTS
    op.execute(sa.schema.CreateSequence(sa.Sequence('proc_event_id_seq')))
    op.create_table('PROCESSING_EVENTS',
        sa.Column('id', sa.Integer(), sa.Sequence('proc_event_id_seq'), primary_key=True),
        sa.Column('job_id', sa.String(length=36), nullable=False),
        sa.Column('pipeline_run_id', sa.String(length=36), nullable=True),
        sa.Column('page_id', sa.String(length=36), nullable=True),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('message', sa.String(length=2000), nullable=True),
        sa.Column('metadata_json', OracleJSON(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['PROCESSING_JOBS.id'], ),
        sa.ForeignKeyConstraint(['page_id'], ['DOCUMENT_PAGES.id'], ),
        sa.ForeignKeyConstraint(['pipeline_run_id'], ['PIPELINE_RUNS.id'], )
    )
    
    # DEPARTMENT_URLS
    op.execute(sa.schema.CreateSequence(sa.Sequence('dept_urls_id_seq')))
    op.create_table('DEPARTMENT_URLS',
        sa.Column('id', sa.Integer(), sa.Sequence('dept_urls_id_seq'), primary_key=True),
        sa.Column('department_id', sa.Integer(), nullable=False),
        sa.Column('url', sa.String(length=2000), nullable=False),
        sa.Column('label', sa.String(length=500), nullable=True),
        sa.Column('template_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_status', sa.String(length=50), nullable=True),
        sa.Column('last_status_code', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['department_id'], ['DEPARTMENTS.id'], ),
        sa.ForeignKeyConstraint(['template_id'], ['TEMPLATES.id'], )
    )

    # TEMPLATE_FIELDS
    op.execute(sa.schema.CreateSequence(sa.Sequence('tmpl_fields_id_seq')))
    op.create_table('TEMPLATE_FIELDS',
        sa.Column('id', sa.Integer(), sa.Sequence('tmpl_fields_id_seq'), primary_key=True),
        sa.Column('template_id', sa.Integer(), nullable=False),
        sa.Column('field_name', sa.String(length=500), nullable=False),
        sa.Column('field_type', sa.String(length=100), nullable=True),
        sa.Column('is_required', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('confidence_threshold', sa.Float(), nullable=False, server_default='0.7'),
        sa.Column('validation_regex', sa.String(length=2000), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('extraction_hint', sa.Text(), nullable=True),
        sa.Column('field_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['template_id'], ['TEMPLATES.id'], )
    )


def downgrade() -> None:
    op.drop_table('TEMPLATE_FIELDS')
    op.drop_table('DEPARTMENT_URLS')
    op.drop_table('PROCESSING_EVENTS')
    op.drop_table('TABLE_EXTRACTIONS')
    op.drop_table('FIELD_EXTRACTIONS')
    op.drop_table('PIPELINE_RUNS')
    op.drop_table('DOCUMENT_PAGES')
    op.drop_table('ASSETS')
    op.drop_column('PROCESSING_JOBS', 'page_count')
    op.drop_column('PROCESSING_JOBS', 'pipeline_mode')
    op.drop_column('DOCUMENTS', 'page_count')
    
    op.execute(sa.text('DROP SEQUENCE tmpl_fields_id_seq'))
    op.execute(sa.text('DROP SEQUENCE dept_urls_id_seq'))
    op.execute(sa.text('DROP SEQUENCE proc_event_id_seq'))
    op.execute(sa.text('DROP SEQUENCE table_ext_id_seq'))
    op.execute(sa.text('DROP SEQUENCE field_ext_id_seq'))
