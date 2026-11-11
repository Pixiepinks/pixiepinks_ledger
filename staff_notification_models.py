"""Internal staff alerts and encrypted device subscriptions; no customer sends."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON, UniqueConstraint, Index, CheckConstraint
from database import Base


class StaffNotificationPreference(Base):
    __tablename__ = 'staff_notification_preferences'
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    messages = Column(Boolean, nullable=False, default=True)
    assignments = Column(Boolean, nullable=False, default=True)
    reminders = Column(Boolean, nullable=False, default=True)
    sound = Column(Boolean, nullable=False, default=False)


class StaffNotificationControl(Base):
    __tablename__ = 'staff_notification_control'
    id = Column(Integer, primary_key=True)
    shared_users = Column(JSON, nullable=False, default=list)
    escalation_users = Column(JSON, nullable=False, default=list)
    reminders_enabled = Column(Boolean, nullable=False, default=False)
    escalations_enabled = Column(Boolean, nullable=False, default=False)
    reminder_minutes = Column(Integer, nullable=False, default=5)
    escalation_minutes = Column(Integer, nullable=False, default=15)
    updated_by = Column(Integer, ForeignKey('users.id'))
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (CheckConstraint('id = 1', name='ck_staff_notification_control_singleton'),
        CheckConstraint('reminder_minutes >= 1 AND escalation_minutes > reminder_minutes AND escalation_minutes <= 1440', name='ck_staff_notification_intervals'),)


class StaffPushDevice(Base):
    __tablename__ = 'staff_push_devices'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    endpoint_hash = Column(String(64), nullable=False, unique=True)
    browser_hash = Column(String(64), nullable=False, index=True)
    subscription_encrypted = Column(Text, nullable=False)
    label = Column(String(80), nullable=False)
    session_version = Column(Integer, nullable=False)
    vapid_key_hash = Column(String(64), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_status = Column(String(24))


class StaffNotificationInbound(Base):
    """Live-message marker, independent of recipient preferences or device registration."""
    __tablename__ = 'staff_notification_inbounds'
    message_id = Column(Integer, ForeignKey('whatsapp_conversation_messages.id'), primary_key=True)
    conversation_id = Column(Integer, ForeignKey('whatsapp_conversations.id'), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class StaffNotification(Base):
    __tablename__ = 'staff_notifications'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    event_key = Column(String(160), nullable=False)
    kind = Column(String(20), nullable=False)
    conversation_id = Column(Integer, ForeignKey('whatsapp_conversations.id'))
    message_id = Column(Integer, ForeignKey('whatsapp_conversation_messages.id'))
    actor_id = Column(Integer, ForeignKey('users.id'))
    assignment_version = Column(Integer)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    read_at = Column(DateTime)
    expanded = Column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint('user_id', 'event_key', name='uq_staff_notification_event'),
                     Index('ix_staff_notifications_recent', 'user_id', 'created_at'),
                     CheckConstraint("kind IN ('MESSAGE','ASSIGNMENT','REMINDER','ESCALATION','TEST')", name='ck_staff_notification_kind'),)


class StaffPushDelivery(Base):
    __tablename__ = 'staff_push_deliveries'
    id = Column(Integer, primary_key=True)
    notification_id = Column(Integer, ForeignKey('staff_notifications.id'), nullable=False)
    device_id = Column(Integer, ForeignKey('staff_push_devices.id'), nullable=False)
    status = Column(String(16), nullable=False, default='PENDING')
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    lease_until = Column(DateTime)
    last_code = Column(String(24))
    __table_args__ = (UniqueConstraint('notification_id', 'device_id', name='uq_staff_push_delivery'),
                     Index('ix_staff_push_due', 'status', 'next_attempt_at'),
                     CheckConstraint("status IN ('PENDING','SENDING','SENT','FAILED','EXPIRED','CANCELED')", name='ck_staff_push_status'),
                     CheckConstraint('attempts >= 0 AND attempts <= 5', name='ck_staff_push_attempts'),)
