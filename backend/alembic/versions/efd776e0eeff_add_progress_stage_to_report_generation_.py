"""add progress_stage to report generation jobs

Revision ID: efd776e0eeff
Revises: bab48da61fd8
Create Date: 2026-09-02 14:38:59.078125

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'efd776e0eeff'
down_revision = 'bab48da61fd8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Autogenerate also picked up an unrelated pre-existing drift (a
    # missing 'ix_site_audit_runs_client_id' index) — left untouched here,
    # not part of this change.
    op.add_column('report_generation_jobs', sa.Column('progress_stage', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('report_generation_jobs', 'progress_stage')
