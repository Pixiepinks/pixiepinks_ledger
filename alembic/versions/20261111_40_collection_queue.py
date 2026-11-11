"""Durable browser-independent staff product collection queue."""
from alembic import op
revision = '20261111_40'
down_revision = '20261111_39'
branch_labels = None
depends_on = None


def upgrade():
    from collection_send_queue import CollectionSendJob
    CollectionSendJob.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    pass  # Retain progress; stop queue workers before application rollback.
