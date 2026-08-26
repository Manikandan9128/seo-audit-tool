"""add is_own_site and domain_label to semrush_imports

Revision ID: 7552cb3916d0
Revises: f3a9c1d5e7b2
Create Date: 2026-08-24 21:01:57.722342

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7552cb3916d0'
down_revision = 'f3a9c1d5e7b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'semrush_imports',
        sa.Column('is_own_site', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column('semrush_imports', 'is_own_site', server_default=None)
    op.add_column('semrush_imports', sa.Column('domain_label', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('semrush_imports', 'domain_label')
    op.drop_column('semrush_imports', 'is_own_site')
