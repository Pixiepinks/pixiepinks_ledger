"""Explicit order service charge for confirmed customer quotes."""
from alembic import op
import sqlalchemy as sa
revision='20261108_34'
down_revision='20261108_33'
branch_labels=None
depends_on=None


def upgrade():
    if 'service_total' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('sales_orders')}:
        op.add_column('sales_orders',sa.Column('service_total',sa.Numeric(14,2),nullable=False,server_default='0'))


def downgrade():
    pass  # Preserve previously verified order totals and evidence.
