"""add company_overview_cache to clients

Revision ID: c4a1f9d2e6b8
Revises: 85814553f2d0
Create Date: 2026-08-28 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'c4a1f9d2e6b8'
down_revision = '85814553f2d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('clients', sa.Column('company_overview_cache', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('clients', sa.Column('company_overview_cached_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('clients', 'company_overview_cached_at')
    op.drop_column('clients', 'company_overview_cache')
