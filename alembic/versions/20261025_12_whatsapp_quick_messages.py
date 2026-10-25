"""Editable staff quick messages and optional attribution on existing send receipts."""
from datetime import datetime
from alembic import op
import sqlalchemy as sa

revision='20261025_12'
down_revision='20261025_11'
branch_labels=None
depends_on=None

DEFAULTS=[
 ('Website Link','website','General','🌐 Visit our website: https://www.pixiepinks.shop\nExplore our latest products and offers!'),
 ('Free Bicycle Delivery','delivery','Delivery','🚚 We offer FREE island-wide delivery for bicycles.'),
 ('Order Information','orderinfo','Orders',"📝 To place your order, please provide:\n\n1. Contact person's name\n2. Delivery address\n3. Two contact numbers"),
 ('Thank You','thankyou','General','💖 Thank you for contacting PixiePinks!'),
]


def upgrade():
    bind=op.get_bind()
    if not sa.inspect(bind).has_table('whatsapp_quick_messages'):
        op.create_table('whatsapp_quick_messages',
            sa.Column('id',sa.Integer(),primary_key=True),
            sa.Column('title',sa.String(100),nullable=False),
            sa.Column('shortcut',sa.String(40),nullable=False,unique=True),
            sa.Column('category',sa.String(80),nullable=False),
            sa.Column('language',sa.String(2),nullable=False),
            sa.Column('body',sa.Text(),nullable=False),
            sa.Column('active',sa.Boolean(),nullable=False,server_default=sa.true()),
            sa.Column('display_order',sa.Integer(),nullable=False,server_default='0'),
            sa.Column('usage_count',sa.Integer(),nullable=False,server_default='0'),
            sa.Column('created_at',sa.DateTime(),nullable=False),
            sa.Column('updated_at',sa.DateTime(),nullable=False),
            sa.Column('created_by',sa.String(100)),sa.Column('updated_by',sa.String(100)))
    if 'quick_message_id' not in {column['name'] for column in sa.inspect(bind).get_columns('whatsapp_manual_sends')}:
        with op.batch_alter_table('whatsapp_manual_sends') as batch:
            batch.add_column(sa.Column('quick_message_id',sa.Integer(),nullable=True))
            batch.create_foreign_key('fk_whatsapp_manual_send_quick_message','whatsapp_quick_messages',['quick_message_id'],['id'],ondelete='RESTRICT')
    table=sa.table('whatsapp_quick_messages',*[sa.column(name) for name in ['title','shortcut','category','language','body','active','display_order','usage_count','created_at','updated_at','created_by','updated_by']])
    for order,(title,shortcut,category,body) in enumerate(DEFAULTS):
        # Seed once, never replace edits, activation state, usage or history.
        if bind.execute(sa.select(table.c.shortcut).where(table.c.shortcut==shortcut)).first():continue
        bind.execute(table.insert().values(title=title,shortcut=shortcut,category=category,language='en',body=body,
            active=True,display_order=order,usage_count=0,created_at=datetime.utcnow(),updated_at=datetime.utcnow(),created_by='migration',updated_by='migration'))


def downgrade():
    raise RuntimeError('Quick messages, usage and send attribution are retained. Roll back application behavior with this migration kept in the graph; do not destroy staff history.')
