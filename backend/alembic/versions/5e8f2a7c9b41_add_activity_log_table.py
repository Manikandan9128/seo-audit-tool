"""add activity_log table

Revision ID: 5e8f2a7c9b41
Revises: c3d9e1f2a4b5
Create Date: 2026-09-30 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '5e8f2a7c9b41'
down_revision = 'c3d9e1f2a4b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'activity_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('user_name', sa.String(), nullable=False, server_default=''),
        sa.Column('user_email', sa.String(), nullable=False, server_default=''),
        sa.Column('user_role', sa.String(), nullable=False, server_default=''),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('client_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('clients.id'), nullable=True),
        sa.Column('client_name', sa.String(), nullable=True),
        sa.Column('detail', postgresql.JSONB(), nullable=True),
    )
    op.create_index('ix_activity_log_created_at', 'activity_log', ['created_at'])
    op.create_index('ix_activity_log_user_id', 'activity_log', ['user_id'])
    op.create_index('ix_activity_log_action', 'activity_log', ['action'])
    op.create_index('ix_activity_log_client_id', 'activity_log', ['client_id'])


def downgrade() -> None:
    op.drop_table('activity_log')
