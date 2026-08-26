"""add site_audit_runs table

Revision ID: f3a9c1d5e7b2
Revises: 2ec3cee90792
Create Date: 2026-08-24 16:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f3a9c1d5e7b2'
down_revision = '2ec3cee90792'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('site_audit_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('client_id', sa.UUID(), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_site_audit_runs_client_id', 'site_audit_runs', ['client_id'])


def downgrade() -> None:
    op.drop_index('ix_site_audit_runs_client_id', table_name='site_audit_runs')
    op.drop_table('site_audit_runs')
