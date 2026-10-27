"""Persistent saved voice library and durable staff send claims."""
from alembic import op

revision = "20261027_17"
down_revision = "20261026_16"
branch_labels = None
depends_on = None


def upgrade():
    from models import SavedVoice, SavedVoiceSend
    SavedVoice.__table__.create(op.get_bind(), checkfirst=True)
    SavedVoiceSend.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    # Audio remains on persistent storage; no file is deleted by schema rollback.
    op.drop_table("saved_voice_sends")
    op.drop_table("saved_voices")
