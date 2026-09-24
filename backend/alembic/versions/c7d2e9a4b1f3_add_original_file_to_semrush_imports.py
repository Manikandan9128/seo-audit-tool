"""add original_file to semrush imports

Revision ID: c7d2e9a4b1f3
Revises: a4c8e2f61d90
Create Date: 2026-09-24 18:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7d2e9a4b1f3'
down_revision = 'a4c8e2f61d90'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('semrush_imports', sa.Column('original_file', sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column('semrush_imports', 'original_file')
