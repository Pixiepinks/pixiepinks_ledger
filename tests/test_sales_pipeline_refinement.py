from datetime import datetime,timedelta,date
from decimal import Decimal
from types import SimpleNamespace
import pytest
from models import Lead,SalesOrder,User,WhatsAppConversation as C
from sales_pipeline_models import SalesOpportunity as O
import sales_pipeline as s
from test_sales_pipeline import fixture,inbox,change,pg

@pytest.mark.parametrize('status,label',[('AWAITING_CUSTOMER_REPLY','Awaiting Customer Reply'),('OUT_OF_STOCK','Out of Stock'),('NOT_RESPONDING','Not Responding'),('PAID','Paid — Verification Required')])
def test_readable_status_does_not_change_canonical_values(status,label):assert s.display_status(status,None)==label


def test_date_urgency_calendar_boundaries():
    now=date(2026,11,26)
    for value,kind in [('2026-05-19','overdue'),('2026-11-26','today'),('2026-11-27','tomorrow'),('2026-12-01','upcoming'),(None,'unknown')]:assert s.date_state(value,now)['kind']==kind


def test_sort_before_column_pagination_and_unknowns_visible(fixture):
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:
        user=db.query(User).filter_by(username='quick-admin').one()
        for n in range(60):
            c=C(phone_number=f'9477099{n:04}',customer_name='Synthetic '+str(n),assigned_to_user_id=user.id,last_customer_message_at=datetime(2026,11,1)+timedelta(days=n));db.add(c);db.flush()
            l=Lead(customer_name=c.customer_name,mobile=c.phone_number,whatsapp_conversation_id=c.id,status='INTERESTED');db.add(l);db.flush()
            s.ensure(db,l,user.id)
        db.commit()
    first=client.get('/crm/sales-pipeline/api?sort=latest').json();expanded=client.get('/crm/sales-pipeline/api?sort=latest&limit_INTERESTED=100').json()
    assert len(first['columns']['INTERESTED'])==50 and len(expanded['columns']['INTERESTED'])==61
    assert [v['id'] for v in first['columns']['INTERESTED']]==[v['id'] for v in expanded['columns']['INTERESTED'][:50]]
    assert first['kpi']==expanded['kpi']
    assert client.get('/crm/sales-pipeline/api?sort=invalid').status_code==422
    assert client.get('/crm/sales-pipeline/api?limit_INTERESTED=1001').status_code==422


def test_sort_priority_value_and_unknown_dates():
    rows=[{'id':i,'stage':'PAYMENT','activity_at':'2026-11-26T00:00:00Z','priority':p,'estimated':v,'payment_date':d,'assigned_id':a} for i,p,v,d,a in [(1,'LOW',None,None,None),(2,'HIGH','20','2026-11-24',1),(3,'MEDIUM','100','2026-12-01',1)]]
    s.sort_items(rows,'priority');assert [v['id'] for v in rows]==[2,3,1]
    s.sort_items(rows,'value');assert [v['id'] for v in rows]==[3,2,1]
    s.sort_items(rows,'default');assert [v['id'] for v in rows]==[2,3,1]
    s.sort_items(rows,'unassigned');assert rows[0]['id']==1


def test_order_reconciliation_readonly_missing_ambiguous_and_linked(fixture):
    from sales_pipeline_diagnostics import order_reconciliation
    client,sessions,ids,csrf,oid=fixture
    with sessions() as db:
        lead=db.query(Lead).one();lead.status='ORDER_CONFIRMED';db.commit()
        assert order_reconciliation(db)['counts']['NO_EXISTING_ORDER_ASSOCIATION']==1
        for n in range(2):db.add(SalesOrder(internal_order_number='SYNTH-REVIEW-'+str(n),idempotency_key='synthetic-review-'+str(n),crm_conversation_id=ids[0],customer_name='Synthetic',whatsapp_phone='94770012301',created_by='test',order_status='CONFIRMED',payment_status='UNPAID',fulfillment_status='UNFULFILLED',subtotal=1000,grand_total=1000,amount_paid=0,currency='LKR'))
        db.commit();before=db.query(SalesOrder).count()
        value=order_reconciliation(db,details=True);assert value['counts']['AMBIGUOUS_ORDER_CANDIDATES']==1
        assert db.query(SalesOrder).count()==before and db.get(O,oid).order_id is None
        order=db.query(SalesOrder).first();o=db.get(O,oid);o.order_id=order.id;o.data={**o.data,'order_id':order.id};db.commit()
        result=order_reconciliation(db,details=True)
        assert result['counts']['VERIFIED_LOCAL_ORDER_ASSOCIATION']==1 and result['details'][0]['displayed_in_confirmed'] is True
        lead.status='PAID';db.commit();result=order_reconciliation(db,details=True)
        assert result['details'][0]['display_exclusion']=='LEAD_STATUS_REMAINS_PAYMENT_PENDING'


@pytest.mark.parametrize('count',[1000,5000])
def test_scale_bounded_queries_complete_kpis_and_column_pages(fixture,count):
    import json,time
    from pathlib import Path
    from sqlalchemy import event
    from scripts.seed_sales_pipeline_scale import seed
    client,sessions,ids,csrf,oid=fixture;seed(sessions,count)
    with sessions() as db:
        user=db.query(User).filter_by(username='quick-admin').one();queries=[]
        def measure(*args):queries.append(1)
        event.listen(db.bind,'before_cursor_execute',measure)
        try:
            start=time.perf_counter();result=s.report(db,user,{'sort':'latest'});elapsed=time.perf_counter()-start
        finally:event.remove(db.bind,'before_cursor_execute',measure)
        assert result['kpi']['open']==count+1 and result['counts']['INTERESTED']==int(count*.8)+1
        assert len(result['columns']['INTERESTED'])==50 and len(queries)<=12
        assert elapsed<15  # Disposable development database budget, not a production SLA.
        page=s.report(db,user,{'sort':'latest','offset_INTERESTED':'50'})
        assert not ({v['id'] for v in result['columns']['INTERESTED']}&{v['id'] for v in page['columns']['INTERESTED']})
        assert result['kpi']==page['kpi']
        Path(f'/tmp/pipeline-refinement-performance-{count}.json').write_text(json.dumps({'opportunities':count+1,'interested':result['counts']['INTERESTED'],'projection_seconds':round(elapsed,4),'sql_statements':len(queries),'returned_cards':sum(map(len,result['columns'].values())),'response_bytes':len(json.dumps(result))},indent=2))


def test_postgresql_order_diagnostic_cannot_write(pg,monkeypatch,capsys):
    import sys,json
    from sqlalchemy import text
    from scripts import validate_sales_pipeline_orders_readonly as command
    engine,sessions,uid=pg
    with engine.begin() as c:
        c.execute(text('CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)'));c.execute(text("INSERT INTO alembic_version VALUES ('20261126_61')"))
    monkeypatch.setattr(command,'SessionLocal',sessions);monkeypatch.setattr(sys,'argv',['diagnostic','--details']);command.main()
    result=json.loads(capsys.readouterr().out)
    assert result['transaction_read_only'] and result['orders']['mutations'] is False
    with sessions() as db:assert db.query(O).count()==db.query(SalesOrder).count()==0


def test_console_command_on_existing_schema_is_readonly(pg,monkeypatch,capsys):
    import json,database
    from pathlib import Path
    from sqlalchemy import text
    engine,sessions,uid=pg
    with engine.begin() as c:
        c.execute(text('CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)'));c.execute(text("INSERT INTO alembic_version VALUES ('20261126_61')"))
    monkeypatch.setattr(database,'SessionLocal',sessions)
    body=Path('scripts/sales_pipeline_orders_console.txt').read_text().split("python - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    exec(compile(body,'reviewed_readonly_console','exec'),{})
    result=json.loads(capsys.readouterr().out);assert result['transaction_read_only'] and result['status']=='READ_ONLY_CHECK_COMPLETE'
    with sessions() as db:assert db.query(O).count()==db.query(SalesOrder).count()==0
