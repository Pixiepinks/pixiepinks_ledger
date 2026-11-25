"""Shared operational customer registration; no historical association backfill."""
from alembic import op
import sqlalchemy as sa
revision='20261125_58'
down_revision='20261124_57'
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    cols={c['name'] for c in sa.inspect(bind).get_columns('customers')}
    for name,kind in [('normalized_mobile',sa.String(20)),('registration_source',sa.String(30)),('registered_at',sa.DateTime())]:
        if name not in cols:op.add_column('customers',sa.Column(name,kind,nullable=True))
    if 'ix_customers_normalized_mobile' not in {i['name'] for i in sa.inspect(bind).get_indexes('customers')}:op.create_index('ix_customers_normalized_mobile','customers',['normalized_mobile'])
    if 'unidentified_reason' not in {c['name'] for c in sa.inspect(bind).get_columns('sales_orders')}:op.add_column('sales_orders',sa.Column('unidentified_reason',sa.String(1000)))
    from models import CustomerPhoneLock,CustomerRegistration
    CustomerPhoneLock.__table__.create(bind,checkfirst=True);CustomerRegistration.__table__.create(bind,checkfirst=True)

def downgrade():
    op.drop_table('customer_registrations');op.drop_table('customer_phone_locks')
    op.drop_column('sales_orders','unidentified_reason');op.drop_index('ix_customers_normalized_mobile',table_name='customers')
    for name in ('registered_at','registration_source','normalized_mobile'):op.drop_column('customers',name)
