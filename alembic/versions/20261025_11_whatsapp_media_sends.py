"""Add durable, metadata-only manual media send claims.

Revision ID: 20261025_11
Revises: 20261024_10
"""
from alembic import op
import sqlalchemy as sa

revision = '20261025_11'
down_revision = '20261024_10'
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table('whatsapp_media_sends'):
        return
    op.create_table('whatsapp_media_sends',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('idempotency_key', sa.String(100), nullable=False, unique=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('whatsapp_conversations.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('staff_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('media_id', sa.String(255)),
        sa.Column('message_id', sa.Integer(), sa.ForeignKey('whatsapp_conversation_messages.id', ondelete='RESTRICT')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False))


def downgrade():
    raise RuntimeError('Send receipts must be retained to prevent duplicate customer messages')
