"""Nullable evidence for saved and browser-recorded voice delivery attempts."""
from alembic import op
import sqlalchemy as sa
revision='20261030_20'
down_revision='20261030_19'
branch_labels=None
depends_on=None


def upgrade():
    for table in ('saved_voice_sends','whatsapp_manual_sends'):
        if 'audio_trace' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}:
            op.add_column(table,sa.Column('audio_trace',sa.JSON(),nullable=True))


def downgrade():
    for table in ('saved_voice_sends','whatsapp_manual_sends'):op.drop_column(table,'audio_trace')
