"""Separate delivery phones without rewriting purchaser identities."""
from alembic import op
import sqlalchemy as sa
revision='20261124_57'
down_revision='20261124_56'
branch_labels=None
depends_on=None
def upgrade():
    bind=op.get_bind();cols={c['name'] for c in sa.inspect(bind).get_columns('sales_orders')}
    for name,kind,default in [('delivery_phone',sa.String(30),None),('delivery_alternative_phone',sa.String(30),None),('delivery_country',sa.String(2),'LK'),('delivery_notes',sa.Text(),None),('delivery_method',sa.String(20),'DELIVERY')]:
        if name not in cols:op.add_column('sales_orders',sa.Column(name,kind,nullable=True,server_default=default))
    from order_recipient import local_mobile
    table=sa.table('sales_orders',sa.column('id'),sa.column('whatsapp_phone'),sa.column('alternative_phone'),sa.column('delivery_contact_person'),sa.column('customer_name'),sa.column('delivery_phone'),sa.column('delivery_alternative_phone'))
    for row in bind.execute(sa.select(table)).mappings():
        if not row['delivery_contact_person'] or row['delivery_contact_person'].strip().casefold()!=row['customer_name'].strip().casefold():continue
        try:primary=local_mobile(row['whatsapp_phone'])
        except ValueError:primary=None
        try:alternative=local_mobile(row['alternative_phone'])
        except ValueError:alternative=None
        if primary:bind.execute(table.update().where(table.c.id==row['id']).where(table.c.delivery_phone.is_(None)).values(delivery_phone=primary,delivery_alternative_phone=row['delivery_alternative_phone'] or alternative))
def downgrade():
    for name in ('delivery_method','delivery_notes','delivery_country','delivery_alternative_phone','delivery_phone'):op.drop_column('sales_orders',name)
