from datetime import date,datetime,timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import text
import sales_pipeline as s
from sales_pipeline_models import SalesOpportunity as O,SalesPipelineEvent as E,SalesPipelineReminder as R
from models import Lead,User,WhatsAppConversation as C,SalesOrder,SalesPayment,JournalEntry
from test_whatsapp_quick_messages import inbox
from test_retail_postgres import pg

@pytest.fixture
def fixture(inbox,monkeypatch):
    monkeypatch.setattr(s.settings,'SALES_PIPELINE_ENABLED',True);monkeypatch.setattr(s.settings,'SALES_PIPELINE_REMINDERS_ENABLED',True)
    client,sessions,ids,_,_,csrf=inbox
    with sessions() as db:
        admin=db.query(User).filter_by(username='quick-admin').one();c=db.get(C,ids[0]);c.assigned_to_user_id=admin.id
        l=Lead(lead_no='P-1',customer_name='Synthetic Pipeline Customer',mobile=c.phone_number,whatsapp_conversation_id=c.id,status='INTERESTED',product_interest='Synthetic product');db.add(l);db.commit();o=db.query(O).one();oid=o.id
    return client,sessions,ids,csrf,oid

def change(v,**values):
    client,sessions,ids,csrf,oid=v
    with sessions() as db:version=db.get(O,oid).version
    return client.post(f'/crm/sales-pipeline/api/{oid}',json={'version':version,'stage':'PAYMENT',**values},headers={'X-CSRF-Token':csrf,'Idempotency-Key':str(uuid4())})

def test_status_sync_and_manual_commitment_do_not_change_finances(fixture):
    client,sessions,ids,csrf,oid=fixture
    r=change(fixture,agreed='22410',payment_date=str(s.today()),precision='EXACT',note='Synthetic commitment');assert r.status_code==200,r.text
    with sessions() as db:
        assert db.query(Lead).one().status=='PAYMENT_PENDING'
        assert db.query(SalesOrder).count()==db.query(SalesPayment).count()==db.query(JournalEntry).count()==0
        l=db.query(Lead).one();l.status='FUTURE_BUYER';db.commit()
    d=client.get('/crm/sales-pipeline/api').json();assert d['counts']['FUTURE']==1 and d['kpi']['future']==1
    assert client.get('/crm/sales-pipeline').status_code==200

@pytest.mark.parametrize('value', ['NaN','Infinity','-1','1.001','abc'])
def test_invalid_amounts(fixture,value):assert change(fixture,agreed=value).status_code==422

def test_dates_precision_concurrency_and_permissions(fixture):
    client,sessions,ids,csrf,oid=fixture
    assert change(fixture,payment_date='2026-02-30',precision='EXACT').status_code==422
    r=change(fixture,stage='FUTURE',precision='RANGE',range_start='2026-12-01',range_end='2026-12-31',purchase_date='2026-12-03');assert r.status_code==200,r.text
    assert r.json()['opportunity']['purchase_date'] is None
    assert client.post(f'/crm/sales-pipeline/api/{oid}',json={'stage':'NEW','version':0},headers={'X-CSRF-Token':csrf,'Idempotency-Key':'stale'}).status_code==409
    client.post('/login',data={'username':'quick-agent','password':'password'})
    assert client.get(f'/crm/sales-pipeline/api/{oid}').status_code==404
    assert client.get('/crm/sales-pipeline/backfill').status_code==403


def test_confirmation_requires_existing_authoritative_order(fixture):
    assert change(fixture,stage='CONFIRMED').status_code==409


def test_backfill_is_preview_and_apply_idempotent(fixture):
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:
        db.add(Lead(customer_name='Synthetic Future',mobile='94770012302',whatsapp_conversation_id=ids[1],status='FUTURE_BUYER',created_at=datetime(2026,10,1,9)));db.flush()
        # Simulate historical data recorded while pipeline was disabled.
        old=s.settings.SALES_PIPELINE_ENABLED;s.settings.SALES_PIPELINE_ENABLED=False
        try:db.commit()
        finally:s.settings.SALES_PIPELINE_ENABLED=old
    before=client.get('/crm/sales-pipeline/backfill').json();assert len(before['proposed'])==1
    assert client.post('/crm/sales-pipeline/backfill',json={'digest':'stale','confirmation':'APPLY PIPELINE BACKFILL'},headers={'X-CSRF-Token':csrf}).status_code==409
    result=client.post('/crm/sales-pipeline/backfill',json={'digest':before['digest'],'confirmation':'APPLY PIPELINE BACKFILL'},headers={'X-CSRF-Token':csrf});assert result.status_code==200,result.text
    assert client.get('/crm/sales-pipeline/backfill').json()['proposed']==[]
    with sessions() as db:assert db.query(O).filter_by(conversation_id=ids[1]).one().created_at==datetime(2026,10,1,9)
    rollback=client.get('/crm/sales-pipeline/backfill/rollback',params={'digest':before['digest']}).json();assert len(rollback['unchanged_ids'])==1
    restored=client.post('/crm/sales-pipeline/backfill/rollback',json={'digest':before['digest'],'confirmation':'ARCHIVE UNCHANGED BACKFILL'},headers={'X-CSRF-Token':csrf});assert restored.status_code==200,restored.text
    with sessions() as db:assert db.query(Lead).filter_by(whatsapp_conversation_id=ids[1]).one().status=='FUTURE_BUYER'


def test_reminders_deduplicate_supersede_and_do_not_send_customers(fixture,monkeypatch):
    client,sessions,ids,csrf,oid=fixture
    assert change(fixture,payment_date=str(s.today()),precision='EXACT').status_code==200
    from staff_notification_models import StaffNotification
    with sessions() as db:
        s.scan(db,datetime.utcnow()+timedelta(days=2));db.commit();count=db.query(StaffNotification).filter(StaffNotification.event_key.like('pipeline:%')).count()
        s.scan(db,datetime.utcnow()+timedelta(days=2));db.commit();assert db.query(StaffNotification).filter(StaffNotification.event_key.like('pipeline:%')).count()==count
        assert count>0
    assert change(fixture,stage='CLOSED').status_code==200
    with sessions() as db:
        import staff_notifications as n
        for notice in db.query(StaffNotification).filter(StaffNotification.event_key.like('pipeline:%')):assert not n.deliverable(db,notice,db.get(User,notice.user_id))


def test_due_labels_timezone():
    assert s.due_label('2026-11-26',date(2026,11,26))=='Due today'
    assert s.due_label('2026-11-25',date(2026,11,26))=='Overdue by 1 days'
    assert s.due_label(None)=='Date not confirmed'


def test_postgresql_unique_active_and_concurrent_version(pg,monkeypatch):
    engine,sessions,uid=pg;monkeypatch.setattr(s.settings,'SALES_PIPELINE_ENABLED',True)
    with sessions() as db:
        c=C(phone_number='94771231234',assigned_to_user_id=uid);db.add(c);db.flush();l=Lead(customer_name='Synthetic PG',mobile=c.phone_number,status='NEW',whatsapp_conversation_id=c.id);db.add(l);db.commit();oid=db.query(O).one().id
    def edit(i):
        with sessions() as db:
            try:s.update(db,db.get(User,uid),oid,{'version':0,'stage':'INTERESTED','note':str(i)},str(uuid4()));db.commit();return True
            except HTTPException:db.rollback();return False
    with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(edit,range(2)))
    assert sorted(result)==[False,True]


def test_partial_payment_totals_are_authoritative_and_orders_not_created(fixture):
    from datetime import date
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:
        order=SalesOrder(internal_order_number='PIPELINE-PARTIAL',idempotency_key='pipeline-partial',crm_conversation_id=ids[0],customer_name='Synthetic',whatsapp_phone='94770012301',created_by='test',order_status='AWAITING_PAYMENT',payment_status='PARTIALLY_PAID',fulfillment_status='UNFULFILLED',subtotal=1000,grand_total=1000,amount_paid=400,currency='LKR');db.add(order);db.flush()
        db.add(SalesPayment(sales_order_id=order.id,payment_method='BANK_TRANSFER',amount=400,received_amount=450,payment_date=date.today(),verification_status='VERIFIED',currency='LKR'));db.commit();orderid=order.id
    r=change(fixture,order_id=orderid,agreed='9999',precision='UNKNOWN');assert r.status_code==200,r.text
    d=client.get('/crm/sales-pipeline/api').json();v=d['columns']['PAYMENT'][0]
    assert v['verified']=='400.00' and v['outstanding']=='600.00' and d['kpi']['pending_value']=='600.00'
    assert v['order']['confirmed'] is False and d['report']['confirmed_order_value']=='0'


def test_inbound_does_not_reset_or_duplicate_and_historical_intents(fixture):
    from models import WhatsAppConversationMessage
    client,sessions,ids,csrf,oid=fixture
    assert change(fixture,stage='FUTURE',estimated='1500').status_code==200
    with sessions() as db:
        c=db.get(C,ids[0]);db.add(WhatsAppConversationMessage(phone_number=c.phone_number,direction='inbound',message_text='Synthetic new inquiry',whatsapp_message_id='wamid.pipeline-test'));db.commit()
        assert db.query(O).count()==1 and db.query(Lead).one().status=='FUTURE_BUYER'
    assert change(fixture,stage='CLOSED',closed_reason='Customer declined').status_code==200
    path=f'/crm/sales-pipeline/api/conversations/{ids[0]}/new';headers={'X-CSRF-Token':csrf}
    a=client.post(path,json={'new_intent':True},headers=headers);assert a.status_code==200,a.text
    b=client.post(path,json={'new_intent':True},headers=headers);assert a.json()['opportunity']['id']==b.json()['opportunity']['id']
    with sessions() as db:assert db.query(O).count()==2


def test_feature_disabled_and_transaction_rollback(fixture,monkeypatch):
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:
        o=db.get(O,oid);uid=db.query(User).filter_by(username='quick-admin').one().id
        s.update(db,db.get(User,uid),oid,{'version':o.version,'stage':'PAYMENT','agreed':'500'},'rollback');db.rollback()
        assert db.get(O,oid).data['stage']=='INTERESTED'
    monkeypatch.setattr(s.settings,'SALES_PIPELINE_ENABLED',False)
    assert client.get('/crm/sales-pipeline/api').status_code==404


def test_postgres_additive_migration_and_guarded_rollback(pg):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import importlib.util
    engine,sessions,uid=pg
    spec=importlib.util.spec_from_file_location('pipeline_migration','alembic/versions/20261126_61_sales_pipeline.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            m.downgrade();m.upgrade();m.upgrade()
    with sessions() as db:
        c=C(phone_number='94770000222');db.add(c);db.flush();l=Lead(customer_name='Synthetic migration',mobile=c.phone_number,whatsapp_conversation_id=c.id,status='NEW');db.add(l);db.flush();s.ensure(db,l,uid);db.commit()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError,match='history exists'):m.downgrade()


def test_notification_history_scope_and_scheduler_idempotency(fixture):
    client,sessions,ids,csrf,oid=fixture
    assert change(fixture,payment_date=str(s.today()),precision='EXACT').status_code==200
    import staff_notifications as n
    from staff_notification_models import StaffNotification
    with sessions() as db:
        s.scan(db,datetime.utcnow()+timedelta(days=1));db.commit()
        user=db.query(User).filter_by(username='quick-admin').one()
        notices=n.history_query(db,user).all()
        assert any(v.event_key.startswith('pipeline:') for v in notices)
        for v in notices:
            if v.event_key.startswith('pipeline:'):assert n.serialize(db,v,user)['url'].startswith('/crm/sales-pipeline?selected=')


def test_audit_update_is_rejected_by_sqlite_migration(fixture):
    from sqlalchemy.exc import IntegrityError
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:
        with pytest.raises(IntegrityError,match='pipeline_audit_immutable'):
            db.execute(text("UPDATE sales_pipeline_events SET key='tamper'"));db.commit()
        db.rollback()


def test_admin_assignment_versions_reminder_settings_and_backfill_rollback(fixture):
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:owner=db.query(User).filter_by(username='quick-staff').one().id
    r=client.post(f'/crm/sales-pipeline/api/{oid}/assign',json={'owner_id':owner,'assignment_version':0},headers={'X-CSRF-Token':csrf});assert r.status_code==200,r.text
    assert client.get(f'/crm/sales-pipeline/api/{oid}').json()['opportunity']['assigned_id']==owner
    assert client.post(f'/crm/sales-pipeline/api/{oid}/assign',json={'owner_id':None,'assignment_version':0},headers={'X-CSRF-Token':csrf}).status_code==409
    p={'hour':9,'minute':30,'payment_offsets':[-1,0,1],'purchase_offsets':[-3,-1,0]}
    r=client.post('/crm/sales-pipeline/settings',json=p,headers={'X-CSRF-Token':csrf});assert r.status_code==200,r.text
    assert client.get('/crm/sales-pipeline/settings').json()['minute']==30


def test_postgres_reminder_races_and_history(pg,monkeypatch):
    from staff_notification_models import StaffNotification
    import staff_notifications as n
    _,sessions,uid=pg
    monkeypatch.setattr(s.settings,'SALES_PIPELINE_ENABLED',True);monkeypatch.setattr(s.settings,'SALES_PIPELINE_REMINDERS_ENABLED',True)
    with sessions() as db:
        c=C(phone_number='94770000333',assigned_to_user_id=uid);db.add(c);db.flush();l=Lead(customer_name='Synthetic reminders',mobile=c.phone_number,whatsapp_conversation_id=c.id,status='PAYMENT_PENDING');db.add(l);db.commit();o=db.query(O).one()
        s.update(db,db.get(User,uid),o.id,{'version':0,'stage':'PAYMENT','payment_date':str(s.today()),'precision':'EXACT'},'pg-reminder');db.commit()
    def scan(_):
        with sessions() as db:s.scan(db,datetime.utcnow()+timedelta(days=2));db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(scan,range(2)))
    with sessions() as db:
        count=db.query(StaffNotification).count();assert count>0 and count==db.query(R).filter_by(state='SENT').count()
        assert len(n.history_query(db,db.get(User,uid)).all())==count


def test_postgres_readonly_verifier(pg,monkeypatch,capsys):
    import importlib.util,json
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from scripts import validate_sales_pipeline_readonly as verifier
    engine,sessions,uid=pg
    spec=importlib.util.spec_from_file_location('pipeline_verify_migration','alembic/versions/20261126_61_sales_pipeline.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):m.upgrade()
        conn.execute(text('CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)'))
        conn.execute(text("INSERT INTO alembic_version VALUES ('20261126_61')"))
    monkeypatch.setattr(verifier,'SessionLocal',sessions);verifier.main()
    result=json.loads(capsys.readouterr().out)
    assert result['transaction_read_only'] is True and result['audit_trigger_present'] is True
    assert result['status']=='READ_ONLY_CHECK_COMPLETE' and result['blockers']==[]
    with sessions() as db:assert db.query(User).count()==1 and db.query(O).count()==0


@pytest.mark.parametrize('remote_state',['SYNCING','SYNC_FAILED','RECONCILIATION_REQUIRED','UNCERTAIN','REVIEW'])
def test_uncertain_existing_order_never_converts_or_recreates(fixture,remote_state):
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:
        order=SalesOrder(internal_order_number='PIPELINE-UNCERTAIN',idempotency_key='pipeline-uncertain',crm_conversation_id=ids[0],customer_name='Synthetic',whatsapp_phone='94770012301',created_by='test',order_status='CONFIRMED',payment_status='PAID',fulfillment_status='UNFULFILLED',subtotal=1000,grand_total=1000,amount_paid=1000,currency='LKR',shopify_sync_status=remote_state);db.add(order);db.commit();orderid=order.id
    assert change(fixture,stage='CONFIRMED',order_id=orderid).status_code==409
    with sessions() as db:assert db.query(SalesOrder).count()==1 and db.query(JournalEntry).count()==0


def test_explicit_flush_before_commit_still_synchronizes(fixture):
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:
        lead=db.query(Lead).one();lead.status='FUTURE_BUYER';db.flush();db.commit()
        assert db.get(O,oid).data['stage']=='FUTURE'


def test_existing_create_order_atomic_link_and_lost_response(fixture,monkeypatch):
    import main
    from test_sales_orders_phase2 import verified,order_payload
    client,sessions,ids,csrf,oid=fixture
    monkeypatch.setattr(main.settings,'SALES_ORDERS_ENABLED',True);monkeypatch.setattr(main,'resolve_inbox_product',verified)
    token=client.get('/api/sales-orders/csrf').json()['csrf_token'];body=order_payload(ids[0])
    headers={'X-CSRF-Token':token,'Idempotency-Key':'browser-random-one','X-Sales-Pipeline-Opportunity':str(oid),'X-Sales-Pipeline-Version':'0'}
    first=client.post('/api/sales-orders',json=body,headers=headers);assert first.status_code==201,first.text
    orderid=first.json()['order']['id']
    def unavailable(*a):raise AssertionError('A retry must not re-read Shopify or create a second draft')
    monkeypatch.setattr(main,'resolve_inbox_product',unavailable)
    headers['Idempotency-Key']='browser-random-two'
    retry=client.post('/api/sales-orders',json=body,headers=headers);assert retry.status_code==200,retry.text
    assert retry.json()['order']['id']==orderid
    with sessions() as db:
        assert db.query(SalesOrder).count()==1 and db.query(JournalEntry).count()==0
        assert db.get(O,oid).order_id==orderid and db.query(Lead).one().status=='INTERESTED'
        order=db.get(SalesOrder,orderid);order.order_status='CONFIRMED';db.commit()
        assert db.query(Lead).one().status=='ORDER_CONFIRMED'


def test_pipeline_order_failure_rolls_back_link(fixture,monkeypatch):
    import main
    from test_sales_orders_phase2 import order_payload
    from shopify_catalog_service import ShopifyCatalogError
    client,sessions,ids,csrf,oid=fixture
    monkeypatch.setattr(main.settings,'SALES_ORDERS_ENABLED',True)
    def unavailable(*a):raise ShopifyCatalogError('Synthetic unavailable catalog')
    monkeypatch.setattr(main,'resolve_inbox_product',unavailable)
    token=client.get('/api/sales-orders/csrf').json()['csrf_token']
    response=client.post('/api/sales-orders',json=order_payload(ids[0]),headers={'X-CSRF-Token':token,'Idempotency-Key':'failed-browser','X-Sales-Pipeline-Opportunity':str(oid),'X-Sales-Pipeline-Version':'0'})
    assert response.status_code>=400
    with sessions() as db:assert db.query(SalesOrder).count()==0 and db.get(O,oid).order_id is None


def test_shared_read_access_cannot_manage_or_export(fixture):
    from models import ConversationShare
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:
        agent=db.query(User).filter_by(username='quick-agent').one()
        db.add(ConversationShare(conversation_id=ids[0],user_id=agent.id,can_reply=True,granted_by=db.get(C,ids[0]).assigned_to_user_id));db.commit()
    client.post('/login',data={'username':'quick-agent','password':'password'})
    token=client.get('/crm/whatsapp/api/work-csrf').json()['csrf_token']
    detail=client.get(f'/crm/sales-pipeline/api/{oid}');assert detail.status_code==200 and detail.json()['can_edit'] is False
    assert client.post(f'/crm/sales-pipeline/api/{oid}',json={'version':0,'stage':'PAYMENT'},headers={'X-CSRF-Token':token,'Idempotency-Key':'shared-change'}).status_code==403
    assert client.get('/crm/sales-pipeline/export').status_code==403


def test_delayed_historical_message_does_not_replace_latest_inquiry(fixture):
    from models import WhatsAppConversationMessage as Message
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:
        c=db.get(C,ids[0]);db.add(Message(phone_number=c.phone_number,direction='inbound',message_text='Recent synthetic inquiry',created_at=datetime.utcnow()));db.commit()
        db.add(Message(phone_number=c.phone_number,direction='inbound',message_text='Delayed old synthetic inquiry',created_at=datetime.utcnow()-timedelta(days=7)));db.commit()
    value=client.get(f'/crm/sales-pipeline/api/{oid}').json()['opportunity']
    assert value['message']=='Recent synthetic inquiry'
