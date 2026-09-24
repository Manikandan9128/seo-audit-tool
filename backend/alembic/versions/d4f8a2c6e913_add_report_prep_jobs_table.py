"""add report_prep_jobs table

Revision ID: d4f8a2c6e913
Revises: c7d2e9a4b1f3
Create Date: 2026-09-24 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'd4f8a2c6e913'
down_revision = 'c7d2e9a4b1f3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'report_prep_jobs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('client_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('clients.id'), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('sections', postgresql.JSONB(), nullable=False),
        sa.Column('progress_pct', sa.Integer(), nullable=True),
        sa.Column('error', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_report_prep_jobs_client_id', 'report_prep_jobs', ['client_id'])


def downgrade() -> None:
    op.drop_index('ix_report_prep_jobs_client_id', table_name='report_prep_jobs')
    op.drop_table('report_prep_jobs')
