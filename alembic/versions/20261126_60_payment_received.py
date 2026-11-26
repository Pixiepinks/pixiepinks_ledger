"""Separate actual receipts from order-applied payments; preserve legacy values."""
from alembic import op
import sqlalchemy as sa
revision = '20261126_60'
down_revision = '20261125_59'
branch_labels = depends_on = None


def upgrade():
    existing = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('sales_payments')}
    if 'received_amount' not in existing:
        op.add_column('sales_payments', sa.Column('received_amount', sa.Numeric(14, 2), nullable=True))
    if 'excess_reason' not in existing:
        op.add_column('sales_payments', sa.Column('excess_reason', sa.String(500), nullable=True))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM sales_payments WHERE received_amount IS NOT NULL AND received_amount <> amount LIMIT 1')).first():
        raise RuntimeError('Retained excess evidence exists; keep revision 60. No payment evidence was removed.')
    op.drop_column('sales_payments', 'excess_reason')
    op.drop_column('sales_payments', 'received_amount')
