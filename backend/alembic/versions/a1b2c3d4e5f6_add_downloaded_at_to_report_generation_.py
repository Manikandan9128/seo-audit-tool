"""add downloaded_at to report generation jobs

Revision ID: a1b2c3d4e5f6
Revises: d4f8a2c6e913
Create Date: 2026-09-25 11:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'd4f8a2c6e913'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('report_generation_jobs', sa.Column('downloaded_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('report_generation_jobs', 'downloaded_at')
