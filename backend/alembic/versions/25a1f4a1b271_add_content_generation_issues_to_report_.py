"""add content_generation_issues to report generation jobs

Revision ID: 25a1f4a1b271
Revises: ff23c8a5edda
Create Date: 2026-09-02 16:09:15.707167

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '25a1f4a1b271'
down_revision = 'ff23c8a5edda'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Autogenerate also picked up the same unrelated pre-existing drift as
    # prior migrations (a missing 'ix_site_audit_runs_client_id' index) —
    # left untouched here, not part of this change.
    op.add_column('report_generation_jobs', sa.Column('content_generation_issues', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('report_generation_jobs', 'content_generation_issues')
