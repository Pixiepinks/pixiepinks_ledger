"""Add private per-message Meta advertisement referral metadata.

Revision ID: 20261024_10
Revises: 20261024_09
"""
from alembic import op
import sqlalchemy as sa

revision = "20261024_10"
down_revision = "20261024_09"
branch_labels = None
depends_on = None


def upgrade():
    # The application's startup creates ORM tables before upgrading Alembic.
    # Also support a direct upgrade of an existing PostgreSQL database.
    if sa.inspect(op.get_bind()).has_table("whatsapp_message_referrals"):
        return
    op.create_table(
        "whatsapp_message_referrals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(),
                  sa.ForeignKey("whatsapp_conversation_messages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_id", sa.String(255)),
        sa.Column("source_type", sa.String(32)),
        sa.Column("source_url", sa.String(2048)),
        sa.Column("headline", sa.String(500)),
        sa.Column("body", sa.String(4000)),
        sa.Column("media_type", sa.String(32)),
        sa.Column("image_url", sa.String(2048)),
        sa.Column("video_url", sa.String(2048)),
        sa.Column("thumbnail_url", sa.String(2048)),
        sa.Column("ctwa_clid", sa.String(1024)),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("message_id", name="uq_whatsapp_message_referrals_message_id"),
    )


def downgrade():
    raise RuntimeError("Referral attribution is retained; rollback code must retain this migration revision")
