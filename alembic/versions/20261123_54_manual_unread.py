"""Independent staff unread reminders and indicator colour configuration."""
from alembic import op
import sqlalchemy as sa
revision='20261123_54'
down_revision='20261122_53'
branch_labels=None
depends_on=None


def upgrade():
    c=op.get_bind()
    if 'unread_indicator_colour' not in {v['name'] for v in sa.inspect(c).get_columns('users')}:
        op.add_column('users',sa.Column('unread_indicator_colour',sa.String(16),nullable=False,server_default='blue'))
    # Stable defaults for existing users; no message/conversation data is updated.
    c.execute(sa.text("UPDATE users SET unread_indicator_colour=CASE ((id-1)%5) WHEN 0 THEN 'blue' WHEN 1 THEN 'purple' WHEN 2 THEN 'orange' WHEN 3 THEN 'green' ELSE 'pink' END"))
    from models import ConversationManualUnread,ConversationManualUnreadAudit
    for model in (ConversationManualUnread,ConversationManualUnreadAudit):model.__table__.create(c,checkfirst=True)


def downgrade():
    bind=op.get_bind()
    if bind.dialect.name=='sqlite' and bind.dialect.server_version_info < (3,35,0):
        raise RuntimeError('Retain the additive unread schema or upgrade SQLite before dropping the colour column')
    op.drop_table('conversation_manual_unread_audits')
    op.drop_table('conversation_manual_unread')
    # Rebuilding users would violate existing FK references on SQLite. This
    # independent column can be dropped natively without touching user rows.
    op.drop_column('users','unread_indicator_colour')
