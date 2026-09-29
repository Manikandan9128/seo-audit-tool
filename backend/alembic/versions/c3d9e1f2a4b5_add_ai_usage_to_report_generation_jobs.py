"""add ai_usage to report generation jobs

Revision ID: c3d9e1f2a4b5
Revises: b7e4c2a9d1f0
Create Date: 2026-09-29 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'c3d9e1f2a4b5'
down_revision = 'b7e4c2a9d1f0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('report_generation_jobs', sa.Column('ai_usage', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('report_generation_jobs', 'ai_usage')
