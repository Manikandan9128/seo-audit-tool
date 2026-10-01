"""add is_active to users

Revision ID: e1f2a3b4c5d6
Revises: 5e8f2a7c9b41
"""
from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "5e8f2a7c9b41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")))


def downgrade() -> None:
    op.drop_column("users", "is_active")
