"""add ai_provider and claude_model to report generation jobs

Revision ID: b7e4c2a9d1f0
Revises: a1b2c3d4e5f6
Create Date: 2026-09-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7e4c2a9d1f0'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('report_generation_jobs', sa.Column('ai_provider', sa.String(), nullable=True))
    op.add_column('report_generation_jobs', sa.Column('claude_model', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('report_generation_jobs', 'claude_model')
    op.drop_column('report_generation_jobs', 'ai_provider')
