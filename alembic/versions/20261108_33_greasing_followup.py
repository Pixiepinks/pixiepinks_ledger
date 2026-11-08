"""Retain sent explanation audio without altering the Saved Voices library."""
from alembic import op
import sqlalchemy as sa
revision='20261108_33'
down_revision='20261108_32'
branch_labels=None
depends_on=None


def upgrade():
    if 'audio_snapshot' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('customer_question_product_sends')}:
        op.add_column('customer_question_product_sends',sa.Column('audio_snapshot',sa.JSON(),nullable=True))


def downgrade():
    pass  # Retain playback and send evidence; no existing flow is opted in.
