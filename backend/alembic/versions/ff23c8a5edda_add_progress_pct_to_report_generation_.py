"""add progress_pct to report generation jobs

Revision ID: ff23c8a5edda
Revises: efd776e0eeff
Create Date: 2026-09-02 14:49:55.208609

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ff23c8a5edda'
down_revision = 'efd776e0eeff'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Autogenerate also picked up the same unrelated pre-existing drift as
    # the prior migration (a missing 'ix_site_audit_runs_client_id' index)
    # — left untouched here, not part of this change.
    op.add_column('report_generation_jobs', sa.Column('progress_pct', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('report_generation_jobs', 'progress_pct')
