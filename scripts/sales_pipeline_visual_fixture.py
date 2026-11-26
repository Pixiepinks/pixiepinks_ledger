"""Isolated visual fixtures only. Never point this script at Railway or a live DB."""
import os,sys
from pathlib import Path
from sqlalchemy.engine import make_url
url=make_url(os.environ.get('DATABASE_URL',''))
if url.drivername!='sqlite' or not str(url.database).startswith('/tmp/pipeline-visual'):
    raise SystemExit('Only a disposable /tmp/pipeline-visual*.db SQLite database is permitted')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from datetime import timedelta
import main
from database import Base,engine,SessionLocal
from migrations import upgrade_database
from models import User,Lead,WhatsAppConversation,SalesOrder
from utils_auth import hash_password
import sales_pipeline as s
from sales_pipeline_models import SalesOpportunity as O
main.app.router.on_startup.clear();main.app.router.on_shutdown.clear()
s.settings.SALES_PIPELINE_ENABLED=True;s.settings.SALES_PIPELINE_REMINDERS_ENABLED=False
Base.metadata.create_all(engine);upgrade_database()
with SessionLocal() as db:
    u=User(username='pipeline-visual-admin',password_hash=hash_password('synthetic-visual-only'),role='ADMIN');db.add(u);db.commit()
    for n,status in enumerate(['NEW','INTERESTED','PAYMENT_PENDING','FUTURE_BUYER','ORDER_CONFIRMED']*3):
        c=WhatsAppConversation(phone_number='9477008'+str(n).zfill(4),customer_name=['Mira Test','Theo Test','Jaya Test','Noah Test','Leela Test'][n%5],assigned_to_user_id=u.id);db.add(c);db.flush()
        l=Lead(customer_name=c.customer_name,mobile=c.phone_number,status=status,whatsapp_conversation_id=c.id,product_interest=['School stationery set','Kids craft book','Projector torch','Learning card reader','Colour pencil set'][n%5]);db.add(l);db.commit();o=db.query(O).filter_by(conversation_id=c.id).one()
        d=dict(o.data);d.update(priority=['HIGH','MEDIUM','LOW'][n%3],note='Synthetic customer commitment for visual acceptance. No financial transaction.',precision='EXACT',payment_date=str(s.today()+timedelta(days=n//5-1)) if n%5==2 else None,purchase_date=str(s.today()+timedelta(days=12+n)) if n%5==3 else None,followup_date=str(s.today()+timedelta(days=1)),agreed='2350.00' if n%5==2 else None,estimated='4200.00' if n%5==3 else None)
        if n%5==4:
            order=SalesOrder(internal_order_number='VISUAL-'+str(n),idempotency_key='visual-'+str(n),customer_name=c.customer_name,whatsapp_phone=c.phone_number,crm_conversation_id=c.id,order_status='CONFIRMED',payment_status='UNPAID',fulfillment_status='UNFULFILLED',subtotal=3500,grand_total=3500,amount_paid=0,currency='LKR',created_by=u.username);db.add(order);db.flush();d['order_id']=order.id
        from datetime import date
        from decimal import Decimal
        o.data=d;o.order_id=d.get('order_id')
        for key in ['payment_date','purchase_date','followup_date']:setattr(o,key,date.fromisoformat(d[key]) if d.get(key) else None)
        for key in ['agreed','estimated']:setattr(o,key,Decimal(d[key]) if d.get(key) is not None else None)
        db.commit()
import uvicorn
uvicorn.run(main.app,host='127.0.0.1',port=8866,log_level='warning')
