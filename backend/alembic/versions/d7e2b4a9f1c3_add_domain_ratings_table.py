"""add domain_ratings table

Revision ID: d7e2b4a9f1c3
Revises: c4a1f9d2e6b8
Create Date: 2026-08-28 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'd7e2b4a9f1c3'
down_revision = 'c4a1f9d2e6b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'domain_ratings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('client_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('domain', sa.String(), nullable=False),
        sa.Column('dr', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('domain_ratings')
