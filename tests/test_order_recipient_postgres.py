from pathlib import Path
from sqlalchemy import text,inspect
from alembic import command
from alembic.config import Config
from test_retail_postgres import pg
from models import SalesOrder,Customer
from sales_order_service import create_draft
from test_loyalty import payload

def test_additive_recipient_backfill_and_rollback(pg):
    engine,sessions,uid=pg
    with sessions() as db:
        ids=[]
        for i,(phone,recipient) in enumerate([('94771234567','Existing buyer'),('96550012345','Existing buyer'),('94771234567','Different recipient')]):
            p=payload();p.update(whatsapp_phone=phone,delivery_contact_person=recipient)
            o,_=create_draft(db,p,'test','recipient-migration-'+str(i));ids.append(o.id)
    fields=('delivery_phone','delivery_alternative_phone','delivery_country','delivery_notes','delivery_method')
    with engine.begin() as c:
        for f in fields:c.execute(text('ALTER TABLE sales_orders DROP COLUMN '+f))
    cfg=Config(str(Path(__file__).parents[1]/'alembic.ini'));command.stamp(cfg,'20261124_56');command.upgrade(cfg,'20261124_57')
    with engine.connect() as c:
        rows=c.execute(text('SELECT id,whatsapp_phone,delivery_phone,grand_total FROM sales_orders ORDER BY id')).mappings().all()
        assert rows[0]['delivery_phone']=='+94771234567' and rows[1]['delivery_phone'] is None and rows[2]['delivery_phone'] is None
        assert rows[1]['whatsapp_phone']=='96550012345' and all(r['grand_total']==4500 for r in rows)
    command.downgrade(cfg,'20261124_56')
    with engine.connect() as c:
        assert not set(fields)&{col['name'] for col in inspect(c).get_columns('sales_orders')}
        assert c.execute(text('SELECT count(*) FROM sales_orders')).scalar()==3
