"""Phase22 preflight: reads only. No approval, reconciliation or worker dispatch."""
import hashlib,json,os
from pathlib import Path
from sqlalchemy import inspect,text
from database import SessionLocal
from pos_models import POSOperation,POSCheckout,POSPilotScope
from hardware_models import PrintDevice,HardwareJob
from settings import settings
from scripts.inspect_pos_sales_readonly import read_only_session,ReadOnlyClient,run as inspect_sales,REFERENCES
from scripts.diagnose_pos_order_readonly import ReadOnlyTransport,run as inspect_transport,ORDER_ID
import retail_service


def run(sessions=SessionLocal,client=None,transport=None):
    out={'mode':'READ_ONLY','write_authorized':False,'runtime_commit':os.getenv('RAILWAY_GIT_COMMIT_SHA'),
        'flags':{k:bool(getattr(settings,k)) for k in ['POS_ENABLED','POS_CHECKOUT_WRITE_ENABLED','POS_REFUND_WRITE_ENABLED','POS_REAL_SALE_PILOT_ENABLED','POS_TEST_CHECKOUT_ENABLED','POS_LOCAL_PRINT_ENABLED','PRINT_CONNECTOR_ENCRYPTED_ENABLED','POS_CASH_DRAWER_AUTOMATION_ENABLED']},
        'source_sha256':{n:hashlib.sha256(Path(__file__).resolve().parents[1].joinpath(n).read_bytes()).hexdigest() for n in ['pos_service.py','pos_shopify.py','pos_real_sale_pilot.py','hardware_service.py']}}
    with read_only_session(sessions) as db:
        shop=retail_service.shop();out['shop']=shop
        out['migrations']=[r[0] for r in db.execute(text('SELECT version_num FROM alembic_version'))]
        ops=db.query(POSOperation).filter(POSOperation.shop==shop,POSOperation.status.in_(['QUEUED','DISPATCHING','UNCERTAIN','REVIEW','CONFLICT'])).order_by(POSOperation.updated_at).all()
        out['financial_queue']=[{'id':o.id,'kind':o.kind,'status':o.status,'attempts':o.attempts,'checkout_id':o.checkout_id} for o in ops]
        out['pending_checkout_count']=db.query(POSCheckout).filter(POSCheckout.shop==shop,POSCheckout.status.notin_(['COMPLETE','CANCELLED'])).count()
        s=db.get(POSPilotScope,shop) if 'pos_pilot_scopes' in inspect(db.connection()).get_table_names() else None
        out['pilot_scope']={'shop':s.shop,'register_id':s.register_id,'device_id':s.device_id,'location':s.location_id} if s else None
        ds=db.query(PrintDevice).filter_by(shop=shop).all()
        out['devices']=[{'id':d.id,'register_id':d.register_id,'status':d.status,'version':d.version,'media':d.media,'last_seen':str(d.last_seen),
            'receipt_only':bool(d.config.get('receipt_only')),'auto_receipts':bool(d.config.get('auto_receipts')),'printing_paused':bool(d.config.get('printing_paused')),
            'outstanding_jobs':db.query(HardwareJob).filter(HardwareJob.device_id==d.id,HardwareJob.status.in_(['QUEUED','DELIVERED_TO_AGENT','UNKNOWN'])).count()} for d in ds]
        out['financial_domain_clear']=not ops and out['pending_checkout_count']==0
    # Both clients explicitly expose only allowlisted read/search/schema queries.
    out['historical_sales']=inspect_sales(sessions,client or ReadOnlyClient(),REFERENCES)
    out['historical_order_transport']=inspect_transport(sessions,transport or ReadOnlyTransport(),ORDER_ID,('full',))
    out['status']='READ_ONLY_DIAGNOSTIC_COMPLETE'
    out['live_pilot_authorized']=False
    return out


def main():
    try:out=run()
    except Exception:
        print(json.dumps({'status':'READ_ONLY_DIAGNOSTIC_UNAVAILABLE','write_authorized':False,'details_suppressed':True}));return 1
    print(json.dumps(out,indent=2,sort_keys=True));return 0

if __name__=='__main__':raise SystemExit(main())
