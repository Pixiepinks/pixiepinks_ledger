"""Daily POS preflight: SELECT-only, no worker startup, network or hardware calls."""
import json,os
from sqlalchemy import inspect,text
from database import SessionLocal
from pos_models import POSDailyPolicy,POSOperation
from hardware_models import PrintDevice,HardwareJob
from settings import settings
from scripts.inspect_pos_sales_readonly import read_only_session
import retail_service


def run(sessions=SessionLocal):
    out={'mode':'READ_ONLY','write_authorized':False,'runtime_commit':os.getenv('RAILWAY_GIT_COMMIT_SHA'),
        'flags':{k:bool(getattr(settings,k)) for k in ['POS_ENABLED','POS_CHECKOUT_WRITE_ENABLED','POS_DAILY_OPERATIONS_ENABLED','POS_REAL_SALE_PILOT_ENABLED','POS_LOCAL_PRINT_ENABLED','PRINT_CONNECTOR_ENCRYPTED_ENABLED','POS_CASH_DRAWER_AUTOMATION_ENABLED']}}
    with read_only_session(sessions) as db:
        shop=retail_service.shop();out['shop']=shop
        out['migrations']=[r[0] for r in db.execute(text('SELECT version_num FROM alembic_version'))]
        tables=inspect(db.connection()).get_table_names()
        out['daily_schema_present']={'pos_daily_policies','pos_daily_sales'}<=set(tables)
        out['policies']=[{'register_id':p.register_id,'device_id':p.device_id,'device_version':p.device_version,'version':p.version,'enabled':bool(p.enabled)} for p in db.query(POSDailyPolicy).filter_by(shop=shop)] if out['daily_schema_present'] else []
        out['financial_operations']=[{'id':o.id,'kind':o.kind,'status':o.status,'attempts':o.attempts,'checkout_id':o.checkout_id} for o in db.query(POSOperation).filter(POSOperation.shop==shop,POSOperation.status.in_(['QUEUED','DISPATCHING','REVIEW','UNCERTAIN','CONFLICT']))]
        out['devices']=[{'id':d.id,'register_id':d.register_id,'location':d.location_id,'status':d.status,'version':d.version,'media':d.media,'last_seen':str(d.last_seen),'receipt_only':bool(d.config.get('receipt_only')),'auto_receipts':bool(d.config.get('auto_receipts')),'printing_paused':bool(d.config.get('printing_paused')),'jobs':[{'id':j.id,'kind':j.kind,'status':j.status,'attempts':j.attempts} for j in db.query(HardwareJob).filter(HardwareJob.device_id==d.id,HardwareJob.status.in_(['QUEUED','DELIVERED_TO_AGENT','UNKNOWN']))]} for d in db.query(PrintDevice).filter_by(shop=shop)]
    out['status']='READ_ONLY_CHECK_COMPLETE';return out


def main():
    try:out=run()
    except Exception:
        print(json.dumps({'status':'READ_ONLY_CHECK_UNAVAILABLE','details_suppressed':True,'write_authorized':False}));return 1
    print(json.dumps(out,indent=2,sort_keys=True));return 0

if __name__=='__main__':raise SystemExit(main())
