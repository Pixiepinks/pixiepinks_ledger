"""Financial report acceptance using real POS postings and mocked Shopify only."""
from datetime import date,datetime,timezone
from zoneinfo import ZoneInfo
from decimal import Decimal

from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos, newcart, cash
from test_retail_postgres import pg
from test_finance_reporting import record
from uuid import uuid4
from models import Account, User, JournalEntry, JournalLine
from pos_models import POSCheckout
import pos_service as service
from finance_reporting import income_statement_totals


def test_actual_pos_sale_partial_refund_corrections_and_report_parity(pos):
    r=pos
    with r.sessions() as db:
        policy,_=service.cfg(db)
        codes={'sales':'4000','discount':'4010','returns':'4020','tax':'2200','cash':'1000','card':'1010','bank':'1100'}
        for role,code in codes.items():
            account=db.get(Account,policy['accounts'][role]);account.code=code
            if role=='tax':account.type='LIABILITY'
        db.commit()
    cid,version=newcart(r,2)
    with r.sessions() as db:
        user=db.get(User,r.uid)
        cart=service.edit_cart(db,user,cid,version,'line',{'id':r.products[0]['id'],'quantity':2,'discount':{'type':'PERCENT','value':'10.00'}},r.pos)
        payload={k:cart.payload[k] for k in ['customer_id','customer_name','phone','invoice_details','order_discount','discount_reason']}
        payload['discount_reason']='Verified report acceptance discount'
        cart=service.edit_cart(db,user,cid,cart.version,'details',payload,r.pos)
        sale=service.checkout(db,user,cid,cart.version,uuid4().hex,cash('15300.00','16000.00'),r.pos);rid=sale.id
    assert service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        refund=service.prepare_refund(db,db.get(User,r.uid),rid,r.sid,uuid4().hex,[{'line':0,'quantity':1}],'CASH',True,'Partial return acceptance');fid=refund.id
    assert service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        service.confirm_payout(db,db.get(User,r.uid),fid,'Confirmed cash refund in disposable test')
        today=datetime.now(ZoneInfo('Asia/Colombo')).date();got=income_statement_totals(db,today,today)
        assert got['gross_sales']==Decimal('17000.00')
        assert got['sales_discounts']==Decimal('850.00')  # 1700 debit less 850 refund reversal.
        assert got['sales_returns']==Decimal('8500.00')
        assert got['net_sales']==Decimal('7650.00')
        assert got['cogs']==0  # No inventory valuation is invented by POS/reporting.
        asset=service.cfg(db)[0]['accounts']['cash'];discount=service.cfg(db)[0]['accounts']['discount']
        def account(code,kind):
            row=Account(code=code,name='Acceptance '+code,type=kind);db.add(row);db.flush();return row.id
        cogs=account('5000','EXPENSE');expense=account('5400','EXPENSE');other=account('4100','INCOME')
        service.post_journal(db,'acceptance-discount-correction',[(asset,Decimal('20'),Decimal(0)),(discount,Decimal(0),Decimal('20'))])
        service.post_journal(db,'acceptance-cogs',[(cogs,Decimal('3500'),Decimal(0)),(asset,Decimal(0),Decimal('3500'))])
        service.post_journal(db,'acceptance-cogs-reversal',[(asset,Decimal('500'),Decimal(0)),(cogs,Decimal(0),Decimal('500'))])
        service.post_journal(db,'acceptance-expense',[(expense,Decimal('70'),Decimal(0)),(asset,Decimal(0),Decimal('70'))])
        service.post_journal(db,'acceptance-expense-reversal',[(asset,Decimal('4'),Decimal(0)),(expense,Decimal(0),Decimal('4'))])
        service.post_journal(db,'acceptance-other-income',[(asset,Decimal('50'),Decimal(0)),(other,Decimal(0),Decimal('50'))])
        service.post_journal(db,'acceptance-other-income-reversal',[(other,Decimal('10'),Decimal(0)),(asset,Decimal(0),Decimal('10'))])
        db.commit()
        before=(db.query(JournalEntry).count(),db.query(JournalLine).count())
        totals=income_statement_totals(db,today.replace(day=1),today)
        assert totals['net_sales']==Decimal('7670.00')
        assert totals['cogs']==Decimal('3000.00')
        assert totals['gross_profit']==Decimal('4670.00')
        assert totals['other_income']==Decimal('40.00')
        assert totals['other_exp']==Decimal('66.00')
        assert totals['net_profit']==Decimal('4644.00')
    dashboard=r.client.get('/dashboard');assert dashboard.status_code==200
    statement=r.client.get('/reports/income-statement',params={'start':today.replace(day=1).isoformat(),'end':today.isoformat()});assert statement.status_code==200
    assert dashboard.context['revenue']==statement.context['total_income']==Decimal('7710.00')
    assert dashboard.context['expenses']==statement.context['total_expenses']==Decimal('3066.00')
    assert dashboard.context['profit']==statement.context['net_profit']==Decimal('4644.00')
    with r.sessions() as db:
        assert before==(db.query(JournalEntry).count(),db.query(JournalLine).count())
        assert db.get(POSCheckout,rid).status=='COMPLETE'
    assert r.remote.mutations==[]


def test_income_statement_date_validation_and_empty_page(pos):
    r=pos
    assert r.client.get('/reports/income-statement').status_code==200
    for params in [{'start':'not-a-date','end':'2026-11-19'},{'start':'2026-11-20','end':'2026-11-19'}]:
        assert r.client.get('/reports/income-statement',params=params).status_code==422


def test_legacy_verified_payment_without_timestamp_still_renders(pos,monkeypatch):
    from models import SalesOrder,SalesPayment
    from settings import settings
    monkeypatch.setattr(settings,'SALES_ORDERS_ENABLED',True)
    r=pos
    with r.sessions() as db:
        row=SalesOrder(internal_order_number='LEGACY-'+uuid4().hex[:12],idempotency_key=uuid4().hex,customer_name='Legacy Customer',whatsapp_phone='94770000000',created_by='test')
        db.add(row);db.flush()
        db.add(SalesPayment(sales_order_id=row.id,payment_method='CASH',amount=100,payment_date=date.today(),verification_status='VERIFIED',source='LEGACY',claimed_by='legacy-clerk',verified_by='legacy-supervisor',verified_at=None))
        db.commit();rid=row.id
    response=r.client.get('/crm/sales-orders/'+str(rid))
    assert response.status_code==200
    assert 'legacy-supervisor' in response.text and 'timestamp unavailable' in response.text


def test_postgres_financial_aggregation_uses_decimal_and_inclusive_dates(pg):
    _,sessions,_=pg
    day=date(2026,11,19)
    with sessions() as db:
        accounts={}
        for code,kind in [('1000','ASSET'),('4000','INCOME'),('4010','INCOME'),('4020','INCOME'),('4100','INCOME'),('5000','EXPENSE'),('5400','EXPENSE')]:
            row=Account(code=code,name='PG '+code,type=kind);db.add(row);db.flush();accounts[code]=row.id
        db.commit()
        record(db,accounts,day,('1000','0.03','0'),('4000','0','0.03'))
        record(db,accounts,day,('1000','0.07','0'),('4000','0','0.07'))
        record(db,accounts,day,('4010','0.01','0'),('1000','0','0.01'))
        record(db,accounts,day,('4020','0.02','0'),('1000','0','0.02'))
        record(db,accounts,date(2026,11,20),('1000','999','0'),('4000','0','999'))
        result=income_statement_totals(db,day,day)
        assert result['gross_sales']==Decimal('0.10')
        assert result['net_profit']==result['net_sales']==Decimal('0.07')
        assert all(isinstance(v,Decimal) for v in result.values())


def test_finance_overview_month_uses_sri_lanka_posting_date(pos,monkeypatch):
    import main
    r=pos
    # 20:00 UTC on Nov 30 is Dec 1 in Sri Lanka, matching POS journal dates.
    instant=datetime(2026,11,30,20,0,tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    class ServerDate(date):
        @classmethod
        def today(cls):return date(2026,11,30)
    monkeypatch.setattr(main,'datetime',Clock);monkeypatch.setattr(main,'date',ServerDate)
    with r.sessions() as db:
        policy,_=service.cfg(db)
        accounts={'cash':policy['accounts']['cash'],'sales':policy['accounts']['sales']}
        record(db,accounts,date(2026,11,30),('cash','100','0'),('sales','0','100'))
        record(db,accounts,date(2026,12,1),('cash','200','0'),('sales','0','200'))
    overview=r.client.get('/dashboard');assert overview.status_code==200
    statement=r.client.get('/reports/income-statement',params={'start':'2026-12-01','end':'2026-12-01'})
    assert statement.status_code==200
    assert overview.context['revenue']==statement.context['total_income']==Decimal('200.00')
    assert overview.context['profit']==statement.context['net_profit']==Decimal('200.00')
