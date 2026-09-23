"""add keyword_intelligence_cache to clients

Revision ID: b8e3f1a7c2d4
Revises: 25a1f4a1b271
Create Date: 2026-09-23 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'b8e3f1a7c2d4'
down_revision = '25a1f4a1b271'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('clients', sa.Column('keyword_intelligence_cache', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('clients', 'keyword_intelligence_cache')
