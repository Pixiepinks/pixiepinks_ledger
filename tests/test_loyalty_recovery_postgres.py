"""Disposable PostgreSQL migration/recovery concurrency; never live CRM."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import text,inspect
from alembic import command
from alembic.config import Config
from models import Customer,SalesOrder,SalesPayment,User,JournalEntry
from loyalty_models import *
from test_loyalty_postgres import pg
from test_loyalty import payload
import loyalty_service as svc
import loyalty_recovery as recovery
import loyalty_shopify_history as history
from test_loyalty_recovery import Reader,node

@pytest.fixture
def recovered_schema(pg,monkeypatch):
    v=pg
    # Base metadata includes new tables; reconstruct genuine revision 55.
    for model in (LoyaltyRecoveryPlan,LoyaltyHistoricalOrder,LoyaltyHistoryScan):model.__table__.drop(v.engine)
    command.upgrade(v.cfg,'20261124_56');command.upgrade(v.cfg,'20261124_56')
    return v


def test_phase2a_additive_upgrade_rollback_preserves_identity_finance(recovered_schema):
    v=recovered_schema
    with v.sessions() as db:
        assert db.get(SalesOrder,v.oid).customer_id==v.cid and db.get(User,v.uid).password_hash=='retained-hash'
        assert db.get(SalesOrder,v.oid).grand_total==4500 and not svc.config(db).enabled
    with v.engine.connect() as c:
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261124_56'
        assert any(u['column_names']==['shop','order_gid'] for u in inspect(c).get_unique_constraints('loyalty_historical_orders'))
    command.downgrade(v.cfg,'20261124_55')
    with v.engine.connect() as c:
        assert 'loyalty_historical_orders' not in inspect(c).get_table_names()
        assert c.execute(text('SELECT customer_id FROM sales_orders WHERE id=:i'),{'i':v.oid}).scalar()==v.cid
        assert c.execute(text('SELECT count(*) FROM sales_payments')).scalar()==1
        assert c.execute(text('SELECT count(*) FROM loyalty_audits')).scalar()==0
    command.upgrade(v.cfg,'20261124_56')


def test_concurrent_approved_recovery_is_single_association(recovered_schema):
    v=recovered_schema
    with v.sessions() as db:
        db.get(SalesOrder,v.oid).customer_id=None;db.commit()
        u=db.get(User,v.uid);job=recovery.preview(db,u,[{'kind':'LOCAL','id':v.oid,'customer_id':v.cid}],'concurrent-recovery');db.commit();jid=job.id;digest=job.digest
        recovery.approve(db,u,job,digest,'Concurrent recovery approved');db.commit()
    def execute(_):
        with v.sessions() as db:
            recovery.require_off(db)
            job=db.query(LoyaltyRecoveryPlan).filter_by(id=jid).populate_existing().with_for_update().one()
            recovery.execute(db,db.get(User,v.uid),job,digest);db.commit();return job.status
    with ThreadPoolExecutor(4) as pool:assert list(pool.map(execute,range(8)))==['COMPLETE']*8
    with v.sessions() as db:
        assert db.query(LoyaltyOrderIdentity).count()==1 and db.query(LoyaltyMember).count()==0
        assert db.get(SalesOrder,v.oid).customer_id is None and db.query(SalesPayment).count()==1
        assert db.query(LoyaltyAudit).filter_by(kind='RECOVERY_EXECUTED').count()==1 and db.query(JournalEntry).count()==0


def test_customer_creation_races_fail_closed_without_duplicates(recovered_schema):
    from fastapi import HTTPException
    v=recovered_schema
    with v.sessions() as db:
        o=db.get(SalesOrder,v.oid);o.customer_id=None;o.customer_name='Reviewed new buyer';o.whatsapp_phone='0779876543';db.commit()
        u=db.get(User,v.uid);jobs=[]
        for i in range(2):
            job=recovery.preview(db,u,[{'kind':'LOCAL','id':v.oid,'action':'CREATE'}],'create-race-'+str(i));db.commit();recovery.approve(db,u,job,job.digest,'Individual verified identity');db.commit();jobs.append((job.id,job.digest))
    def run(value):
        with v.sessions() as db:
            recovery.require_off(db);job=db.query(LoyaltyRecoveryPlan).filter_by(id=value[0]).populate_existing().with_for_update().one()
            try:recovery.execute(db,db.get(User,v.uid),job,value[1]);db.commit();return 200
            except HTTPException as e:db.rollback();return e.status_code
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(run,jobs))==[200,409]
    with v.sessions() as db:
        assert db.query(Customer).count()==2 and db.query(LoyaltyOrderIdentity).count()==1 and db.query(LoyaltyMember).count()==0
        assert db.get(SalesOrder,v.oid).grand_total==4500


def test_live_readonly_diagnostic_has_readonly_transaction_and_no_audit_writes(recovered_schema,monkeypatch):
    import scripts.validate_loyalty_recovery_readonly as diagnostic
    v=recovered_schema;monkeypatch.setattr(diagnostic,'engine',v.engine)
    with v.sessions() as db:before=db.query(LoyaltyAudit).count()
    result=diagnostic.run(False)
    assert result['status']=='READ_ONLY_DIAGNOSTIC_COMPLETE' and result['transaction_read_only'] and result['database_writes']==0
    assert 'Preserved customer' not in str(result) and '0771234567' not in str(result)
    with v.sessions() as db:assert db.query(LoyaltyAudit).count()==before and db.query(LoyaltyMember).count()==0


def test_concurrent_scan_page_cannot_duplicate_evidence(recovered_schema,monkeypatch):
    from fastapi import HTTPException
    v=recovered_schema;monkeypatch.setattr(history.settings,'SHOPIFY_SHOP','loyalty-test')
    with v.sessions() as db:
        job=history.start(db,db.get(User,v.uid),Reader([([node()],True,'c1')]));db.commit();jid=job.id
    def next_page(_):
        with v.sessions() as db:
            recovery.require_off(db);job=db.query(LoyaltyHistoryScan).filter_by(id=jid).populate_existing().with_for_update().one()
            try:history.page(db,db.get(User,v.uid),job,0,Reader([([node()],True,'c1')]));db.commit();return 200
            except HTTPException as e:db.rollback();return e.status_code
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(next_page,range(2)))==[200,409]
    with v.sessions() as db:assert db.query(LoyaltyHistoricalOrder).count()==1 and db.query(SalesOrder).count()==1


def test_copy_paste_console_works_before_phase2a_migration(recovered_schema,monkeypatch,capsys):
    import json
    import database
    v=recovered_schema;command.downgrade(v.cfg,'20261124_55');monkeypatch.setattr(database,'engine',v.engine)
    code=(Path(__file__).parents[1]/'scripts/loyalty-phase2a-readonly-console.txt').read_text().split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    capsys.readouterr();exec(compile(code,'readonly-console','exec'),{})
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='READ_ONLY_LOCAL_DIAGNOSTIC_COMPLETE' and result['transaction_read_only']
    assert result['programme']==[{'enabled':False}] and result['channels'][0]['authoritative_links']==1
    assert 'Preserved customer' not in str(result) and '0771234567' not in str(result)
    with v.sessions() as db:assert db.query(SalesPayment).count()==1 and db.query(LoyaltyAudit).count()==0
    command.upgrade(v.cfg,'20261124_56')
