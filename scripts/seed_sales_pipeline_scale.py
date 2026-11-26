"""Synthetic scale fixture ONLY; never production. Run after visual fixture setup.
DATABASE_URL=sqlite:////tmp/pipeline-visual-*.db python -m scripts.seed_sales_pipeline_scale --count 1000
"""
import os,argparse
from datetime import datetime,timedelta
from sqlalchemy.engine import make_url

def seed(sessions,count=1000):
    from models import User,WhatsAppConversation as C,Lead
    from sales_pipeline_models import SalesOpportunity as O
    with sessions() as db:
        # Fixture setup deliberately writes synthetic snapshots, not customer operations.
        db.info['pipeline_sync']=True
        user=db.query(User).filter_by(role='ADMIN').first()
        cs=[C(phone_number='4477009'+str(n).zfill(7),customer_name=('Synthetic long customer name සිංහල தமிழ் 🌸 ' if n%9==0 else 'Synthetic Buyer ')+str(n),assigned_to_user_id=user.id,last_customer_message_at=datetime(2026,11,26)-timedelta(minutes=n)) for n in range(count)]
        db.add_all(cs);db.flush()
        leads=[Lead(customer_name=c.customer_name,mobile=c.phone_number,whatsapp_conversation_id=c.id,status='INTERESTED' if n<int(count*.8) else ['PAYMENT_PENDING','FUTURE_BUYER','NEW'][n%3],product_interest='Synthetic stationery and learning products',next_followup=datetime(2026,5,19).date() if n%7==0 else None) for n,c in enumerate(cs)]
        db.add_all(leads);db.flush()
        stage={'INTERESTED':'INTERESTED','PAYMENT_PENDING':'PAYMENT','FUTURE_BUYER':'FUTURE','NEW':'NEW'}
        db.add_all([O(conversation_id=c.id,active_conversation_id=c.id,lead_id=l.id,version=0,created_at=l.created_at,data={'stage':stage[l.status],'lead_status':l.status,'priority':['HIGH','MEDIUM','LOW'][n%3],'product':l.product_interest,'note':'Synthetic multilingual message සිංහල පණිවිඩය தமிழ் செய்தி 🌸 — customer requested follow-up; no real sale.','followup_date':l.next_followup.isoformat() if l.next_followup else None,'precision':'UNKNOWN'}) for n,(c,l) in enumerate(zip(cs,leads))])
        db.commit();return {'seeded':count,'interested':int(count*.8)}

if __name__=='__main__':
    url=make_url(os.environ.get('DATABASE_URL',''))
    if url.drivername!='sqlite' or not str(url.database).startswith('/tmp/pipeline-visual'):raise SystemExit('Only disposable /tmp/pipeline-visual*.db permitted')
    parser=argparse.ArgumentParser();parser.add_argument('--count',type=int,default=1000);args=parser.parse_args()
    if not 1000<=args.count<=8000:raise SystemExit('Use 1000–8000 synthetic records')
    from database import SessionLocal
    import json
    print(json.dumps(seed(SessionLocal,args.count)))
