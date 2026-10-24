"""Add metadata-only WhatsApp message attachments.

Revision ID: 20261024_09
Revises: 20261024_08
"""
from alembic import op
import sqlalchemy as sa

revision = "20261024_09"
down_revision = "20261024_08"
branch_labels = None
depends_on = None


def upgrade():
    # Startup creates ORM tables before running migrations. Support both that
    # path and Alembic upgrading an existing production schema directly.
    if sa.inspect(op.get_bind()).has_table("whatsapp_message_attachments"):
        return
    op.create_table(
        "whatsapp_message_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(),
                  sa.ForeignKey("whatsapp_conversation_messages.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("media_id", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(30), nullable=False),
        sa.Column("mime_type", sa.String(100)),
        sa.Column("filename", sa.String(255)),
        sa.Column("caption", sa.Text()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("file_size_bytes", sa.Integer()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("message_id", "media_id", name="uq_whatsapp_attachment_message_media"),
    )
    op.create_index("ix_whatsapp_message_attachments_message_id", "whatsapp_message_attachments", ["message_id"])


def downgrade():
    raise RuntimeError("Attachment metadata is retained; roll back application code without dropping data")
