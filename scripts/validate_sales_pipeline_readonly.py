"""Railway: python -m scripts.validate_sales_pipeline_readonly. No startup imports."""
import json
from sqlalchemy import text,inspect
from database import SessionLocal
from settings import settings
from models import Lead
from sales_pipeline import preview,STAGES

def main():
    with SessionLocal() as db:
        if db.bind.dialect.name!='postgresql':raise RuntimeError('Use PostgreSQL production read-only transaction')
        db.execute(text('SET TRANSACTION READ ONLY'));db.execute(text("SET LOCAL statement_timeout='15s'"))
        inspector=inspect(db.connection());names=['sales_opportunities','sales_pipeline_events','sales_pipeline_reminders','sales_pipeline_control'];existing=inspector.get_table_names()
        available=all(n in existing for n in names)
        result={'mode':'READ_ONLY','transaction_read_only':db.execute(text('SHOW transaction_read_only')).scalar()=='on','migration':db.execute(text('SELECT version_num FROM alembic_version')).scalar(),'flags':{'pipeline':settings.SALES_PIPELINE_ENABLED,'reminders':settings.SALES_PIPELINE_REMINDERS_ENABLED},'tables':{n:n in existing for n in names},'indexes':{n:[v['name'] for v in inspector.get_indexes(n)] for n in names if n in existing},'mappings':STAGES,'notification_tables_ready':all(n in existing for n in ['staff_notifications','staff_push_devices']),'permissions':'Existing WhatsApp conversation visibility/ownership; administrator-only backfill/configuration','live_push_verified':False}
        if available:
            p=preview(db);result['backfill']={'eligible':len(p['proposed']),'distribution':p['distribution'],'unmapped_count':len(p['unmapped_lead_ids']),'missing_commitment_dates':p['missing_commitment_dates'],'digest':p['digest']}
        from models import User
        from staff_access import allowed
        import staff_notifications
        eligible=sum(allowed(u,'whatsapp') and u.is_active for u in db.query(User).filter(User.role.in_(['ADMIN','STAFF','SALES_AGENT','SALES_MANAGER'])))
        result['eligible_staff_count']=eligible
        result['push_transport_configured']=staff_notifications.configured()
        result['audit_trigger_present']=bool(db.execute(text("SELECT 1 FROM pg_trigger WHERE tgname='pipeline_audit_immutable' AND tgrelid='sales_pipeline_events'::regclass")).first()) if available else False
        result['blockers']=([] if result['audit_trigger_present'] else ['AUDIT_TRIGGER_MISSING'])+([] if available else ['PIPELINE_SCHEMA_MISSING'])+([] if eligible else ['NO_ELIGIBLE_STAFF'])
        result['production_activation_requires_approval']=True
        result['status']='READ_ONLY_CHECK_COMPLETE';print(json.dumps(result,default=str,indent=2));db.rollback()
if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'mode':'READ_ONLY','status':'CHECK_UNAVAILABLE','detail':'Read-only schema/runtime verification could not complete; no activation authorized.'}))
        raise SystemExit(1)
