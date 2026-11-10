"""Internal notification outbox, preferences and encrypted web-push devices."""
from alembic import op
import sqlalchemy as sa
revision = '20261111_39'
down_revision = '20261110_38'
branch_labels = None
depends_on = None


def upgrade():
    from staff_notification_models import (StaffNotificationPreference, StaffNotificationControl,
        StaffPushDevice, StaffNotification, StaffPushDelivery, StaffNotificationInbound)
    bind = op.get_bind()
    for model in (StaffNotificationPreference, StaffNotificationControl, StaffPushDevice, StaffNotification, StaffPushDelivery, StaffNotificationInbound):
        model.__table__.create(bind, checkfirst=True)
    table = StaffNotificationControl.__table__
    if not bind.execute(sa.select(table.c.id).where(table.c.id == 1)).first():
        bind.execute(table.insert().values(id=1, shared_users=[], escalation_users=[],
            reminders_enabled=False, escalations_enabled=False, reminder_minutes=5, escalation_minutes=15))


def downgrade():
    pass  # Disable worker before rollback; retain subscriptions/history for audit.
