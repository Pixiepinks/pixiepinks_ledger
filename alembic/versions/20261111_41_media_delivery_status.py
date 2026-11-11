"""Retain numeric-only asynchronous staff-media delivery status/errors."""
from alembic import op
revision = '20261111_41'
down_revision = '20261111_40'
branch_labels = None
depends_on = None


def upgrade():
    from media_delivery_status import MediaDeliveryStatus
    MediaDeliveryStatus.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    pass  # Retain diagnostic evidence; no changes to send claims or message contents.
