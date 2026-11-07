"""Bind manual-send idempotency to the selected quoted message."""
from alembic import op
import sqlalchemy as sa
revision='20261107_27'
down_revision='20261107_26'
branch_labels=None
depends_on=None


def upgrade():
    if 'reply_to_whatsapp_message_id' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('whatsapp_manual_sends')}:
        op.add_column('whatsapp_manual_sends',sa.Column('reply_to_whatsapp_message_id',sa.String(255),nullable=True))


def downgrade():
    # Retain idempotency binding; the old app ignores this nullable column.
    pass
