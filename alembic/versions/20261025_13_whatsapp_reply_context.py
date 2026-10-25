"""Retain the optional Meta reply reference without requiring original history."""
from alembic import op
import sqlalchemy as sa

revision='20261025_13'
down_revision='20261025_12'
branch_labels=None
depends_on=None


def upgrade():
    if 'reply_to_whatsapp_message_id' not in {column['name'] for column in sa.inspect(op.get_bind()).get_columns('whatsapp_conversation_messages')}:
        op.add_column('whatsapp_conversation_messages',sa.Column('reply_to_whatsapp_message_id',sa.String(255),nullable=True))


def downgrade():
    raise RuntimeError('Retain reply references and message history; roll back application behavior with this migration kept in the graph')
