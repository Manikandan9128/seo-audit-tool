"""add keyword_decision_history to clients

Revision ID: a4c8e2f61d90
Revises: b8e3f1a7c2d4
Create Date: 2026-09-24 08:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a4c8e2f61d90'
down_revision = 'b8e3f1a7c2d4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('clients', sa.Column('keyword_decision_history', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('clients', 'keyword_decision_history')
