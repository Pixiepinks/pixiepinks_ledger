"""Issue 200: disposable local databases, mocked inventory only."""
from datetime import date
from uuid import uuid4
from decimal import Decimal
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from alembic import command
from alembic.config import Config
from models import User,Supplier,JournalEntry,SalesOrder
from purchasing_models import PurchaseRecord as Record,PurchaseMovement as Movement,PurchaseAudit as Audit
from inventory_models import InventoryDocument,InventoryEvent
from test_inventory_phase3 import inv,FakeInventory,LOCATION,DESTINATION
from test_retail_phase1 import retail,node,import_nodes
from test_whatsapp_quick_messages import inbox
from test_retail_postgres import pg
from settings import settings
import purchasing_service as svc
import inventory_service as stock
import inventory_shopify as adapter
from retail_inventory import InventoryError

@pytest.fixture
def purchase(inv,monkeypatch):
    monkeypatch.setattr(settings,'PURCHASING_ENABLED',True)
    with inv.sessions() as db:
        user=db.get(User,inv.uid)
        for gid,kind in [(LOCATION,'SHOP'),(DESTINATION,'WAREHOUSE')]:svc.put_directory(db,user,'LOCATION',{'location_id':gid,'type':kind,'enabled':True})
    return inv

def bill_data(r,qty=20):
    p=r.products[0]
    return {'client_key':uuid4().hex,'supplier_id':r.supplier,'invoice':'SUP-'+uuid4().hex,'date':date.today().isoformat(),'location_id':LOCATION,'lines':[{'variant_id':p['id'],'version':p['version'],'quantity':qty,'unit_cost':'100.00','discount':{'type':'FIXED','value':'0'},'tax_rate':'0'}]}

def make_bill(r,qty=20,approved=True,data=None):
    with r.sessions() as db:
        user=db.get(User,r.uid);row=svc.create(db,user,'BILL',data or bill_data(r,qty));rid=row.id
        if approved:
            svc.action(db,user,rid,'submit',rid);svc.action(db,user,rid,'approve',rid)
        return rid

def receive(r,bid,qty,damaged=0,rejected=0,location=LOCATION):
    data={'client_key':uuid4().hex,'bill_id':bid,'delivery_reference':uuid4().hex,'location_id':location,'lines':[{'bill_line':0,'accepted':qty,'damaged':damaged,'rejected':rejected}]}
    with r.sessions() as db:
        user=db.get(User,r.uid);row=svc.create(db,user,'RECEIPT',data);rid=row.id
        svc.action(db,user,rid,'submit',rid);svc.action(db,user,rid,'approve',rid)
        return rid

def enable(monkeypatch):
    monkeypatch.setattr(settings,'PURCHASE_INVENTORY_SYNC_ENABLED',True);monkeypatch.setattr(settings,'SHOPIFY_INVENTORY_WRITE_ENABLED',True)

def dispatch(r,monkeypatch):
    enable(monkeypatch);assert svc.prepare_one(r.sessions,r.remote);assert stock.dispatch_one(r.sessions,r.remote)

def test_invoice_no_stock_then_partial_receipts_exact_once(purchase,monkeypatch):
    r=purchase;bid=make_bill(r)
    ident=('gid://shopify/InventoryItem/1',LOCATION)
    r.remote.values[ident]={'on_hand':5,'available':5}
    assert not r.remote.mutations
    receive(r,bid,8);dispatch(r,monkeypatch)
    assert r.remote.values[ident]['on_hand']==13
    receive(r,bid,12);dispatch(r,monkeypatch)
    assert r.remote.values[ident]['on_hand']==25 and len(r.remote.mutations)==2
    for _ in range(3):assert not svc.prepare_one(r.sessions,r.remote);assert not stock.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:
        assert db.query(Movement).count()==2
        assert all(svc.public(db,s)['movements'][0]['sync_status']=='Synced' for s in db.query(Record).filter_by(kind='RECEIPT'))
        assert db.query(JournalEntry).count()==0 and db.query(SalesOrder).count()==0

def test_create_request_replay_and_approval_replay(purchase):
    r=purchase;d=bill_data(r)
    with r.sessions() as db:
        u=db.get(User,r.uid);a=svc.create(db,u,'BILL',d);b=svc.create(db,u,'BILL',d);assert a.id==b.id
        d['invoice']='different'
        with pytest.raises(HTTPException):svc.create(db,u,'BILL',d)
    bid=make_bill(r);rid=receive(r,bid,20)
    with r.sessions() as db:
        svc.action(db,db.get(User,r.uid),rid,'approve',rid)
        assert db.query(Movement).filter_by(receipt_id=rid).count()==1

@pytest.mark.parametrize('qty,damaged,rejected',[(15,3,2),(0,20,0),(0,0,20)])
def test_damaged_rejected_never_adjust_inventory(purchase,monkeypatch,qty,damaged,rejected):
    r=purchase;rid=receive(r,make_bill(r),qty,damaged,rejected)
    enable(monkeypatch)
    assert svc.prepare_one(r.sessions,r.remote)==bool(qty)
    assert stock.dispatch_one(r.sessions,r.remote)==bool(qty)
    assert len(r.remote.mutations)==bool(qty)
    if qty:assert r.remote.mutations[0][0][0]['after']-r.remote.mutations[0][0][0]['before']==qty

def test_over_receiving_and_void_prevented(purchase):
    r=purchase;bid=make_bill(r);receive(r,bid,19)
    with pytest.raises(HTTPException):receive(r,bid,2)
    with r.sessions() as db:
        with pytest.raises(HTTPException):svc.action(db,db.get(User,r.uid),bid,'void',bid)
        assert db.query(Movement).count()==1

def test_partial_payments_exact_and_no_financial_posting(purchase):
    r=purchase;bid=make_bill(r)
    with r.sessions() as db:
        user=db.get(User,r.uid)
        for amount in ['500.00','1500.00']:
            d={'client_key':uuid4().hex,'bill_id':bid,'amount':amount,'method':'BANK','verified':True,'reference':uuid4().hex,'date':date.today().isoformat()}
            a=svc.create(db,user,'PAYMENT',d);assert svc.create(db,user,'PAYMENT',d).id==a.id
        result=svc.public(db,svc.get(db,bid));assert result['payment_status']=='PAID' and result['balance']=='0.00'
        assert db.query(JournalEntry).count()==0
        with pytest.raises(HTTPException):svc.create(db,user,'PAYMENT',{**d,'client_key':uuid4().hex,'amount':'0.01'})

@pytest.mark.parametrize('mode,total,tax',[('EXCLUSIVE','2260.00','360.00'),('INCLUSIVE','1900.00','280.68'),('NONE','1900.00','0.00')])
def test_invoice_tax_discount_math(purchase,mode,total,tax):
    r=purchase;d=bill_data(r);d.update(tax_mode=mode,discount={'type':'PERCENT','value':'5'});d['lines'][0]['tax_rate']='18'
    # 2,000 less 5%=1,900; exclusive 18%=342.
    if mode=='EXCLUSIVE':total,tax='2242.00','342.00'
    if mode=='INCLUSIVE':tax='289.83'
    with r.sessions() as db:
        q=svc.quote(db,d);assert q['total']==total and q['tax_total']==tax

def test_warehouse_and_concurrent_sale_delta(purchase,monkeypatch):
    r=purchase;receive(r,make_bill(r),20,location=DESTINATION);enable(monkeypatch)
    assert svc.prepare_one(r.sessions,r.remote)
    for v in r.remote.values.values():v['on_hand']-=2;v['available']-=2
    assert stock.dispatch_one(r.sessions,r.remote)
    assert r.remote.mutations[0][0][0]['location_id']==DESTINATION
    assert list(r.remote.values.values())[0]['on_hand']==28

@pytest.mark.parametrize('outcome',['timeout','readback','permissions'])
def test_uncertainty_and_scope_failure_never_resend(purchase,monkeypatch,outcome):
    r=purchase;rid=receive(r,make_bill(r),20);enable(monkeypatch)
    if outcome=='permissions':r.remote.cap_error=True
    elif outcome=='timeout':r.remote.error=TimeoutError()
    assert svc.prepare_one(r.sessions,r.remote)
    if outcome=='readback':
        original=r.remote.apply
        def apply(*a,**k):
            v=original(*a,**k);r.remote.read_error=True;return v
        monkeypatch.setattr(r.remote,'apply',apply)
    stock.dispatch_one(r.sessions,r.remote)
    n=len(r.remote.mutations)
    for _ in range(2):svc.prepare_one(r.sessions,r.remote);stock.dispatch_one(r.sessions,r.remote)
    assert len(r.remote.mutations)==n
    with r.sessions() as db:
        m=db.query(Movement).filter_by(receipt_id=rid).one();doc=db.get(InventoryDocument,m.document_id)
        assert svc.public(db,svc.get(db,rid))['movements'][0]['sync_status']==('Failed' if outcome=='permissions' else 'Needs Review')
        if outcome!='permissions':
            with pytest.raises(HTTPException):svc.retry(db,db.get(User,r.uid),doc.id)

def test_gate_off_even_already_prepared(purchase,monkeypatch):
    r=purchase;receive(r,make_bill(r),20);enable(monkeypatch);assert svc.prepare_one(r.sessions,r.remote)
    monkeypatch.setattr(settings,'PURCHASE_INVENTORY_SYNC_ENABLED',False)
    assert not stock.dispatch_one(r.sessions,r.remote) and not r.remote.mutations

def test_failure_retry_only_before_dispatch(purchase,monkeypatch):
    r=purchase;rid=receive(r,make_bill(r),20);enable(monkeypatch);r.remote.read_error=True
    assert svc.prepare_one(r.sessions,r.remote)
    with r.sessions() as db:
        doc=db.get(InventoryDocument,db.query(Movement).filter_by(receipt_id=rid).one().document_id)
        assert doc.attempts==0 and doc.plan is None
        svc.retry(db,db.get(User,r.uid),doc.id)
    r.remote.read_error=False;assert svc.prepare_one(r.sessions,r.remote);assert stock.dispatch_one(r.sessions,r.remote)
    assert len(r.remote.mutations)==1

def test_api_csrf_permissions_and_screen(purchase,monkeypatch):
    r=purchase
    assert r.client.get('/crm/retail/purchases').status_code==200
    assert r.client.post('/crm/retail/api/purchases/create/BILL',json=bill_data(r)).status_code==403
    with r.sessions() as db:
        u=db.get(User,r.uid);u.role='STAFF';u.section_access=['retail.purchases.view'];db.commit()
    assert r.client.get('/crm/retail/api/purchases/context').status_code==200
    assert r.client.post('/crm/retail/api/purchases/create/BILL',json=bill_data(r),headers={'X-CSRF-Token':r.csrf}).status_code==403
    monkeypatch.setattr(settings,'PURCHASING_ENABLED',False)
    assert r.client.get('/crm/retail/api/purchases/context').status_code==403

def test_receipt_inventory_api_cannot_bypass_receipt_authority(purchase):
    r=purchase;rid=receive(r,make_bill(r),20)
    with r.sessions() as db:
        doc=db.get(InventoryDocument,db.query(Movement).filter_by(receipt_id=rid).one().document_id);u=db.get(User,r.uid)
        with pytest.raises(HTTPException):stock.submit(db,u,doc.id,r.remote)
        with pytest.raises(HTTPException):stock.reject(db,u,doc.id,'Wrong workflow')

@pytest.mark.parametrize('table', ['purchase_audits','purchase_movements','purchase_records'])
def test_migration_immutability(purchase,table):
    r=purchase;receive(r,make_bill(r),20)
    with r.sessions() as db:
        with pytest.raises(Exception):db.execute(text('DELETE FROM '+table));db.flush()
        db.rollback()

def test_postgres_migration_immutable_and_additive(pg):
    engine,sessions,uid=pg
    cfg=Config('alembic.ini');command.stamp(cfg,'20261122_52');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:
        u=db.get(User,uid);db.add(Audit(shop='fixture',actor_id=u.id,action='FIXTURE',detail={}));db.commit()
        with pytest.raises(Exception):db.execute(text('UPDATE purchase_audits SET action=\'tampered\''));db.commit()
        db.rollback();assert db.query(Audit).one().action=='FIXTURE'

def test_catalog_refresh_does_not_retarget_receipt(purchase,monkeypatch):
    r=purchase;receive(r,make_bill(r),20)
    with r.sessions() as db:
        v=db.get(stock.RetailVariant,r.products[0]['id']);v.version+=1;v.price=Decimal('999.00');db.commit()
    dispatch(r,monkeypatch)
    with r.sessions() as db:assert db.get(stock.RetailVariant,r.products[0]['id']).price==Decimal('999.00')

def test_changed_inventory_identity_fails_closed(purchase,monkeypatch):
    r=purchase;receive(r,make_bill(r),20)
    with r.sessions() as db:
        v=db.get(stock.RetailVariant,r.products[0]['id']);v.inventory_item_id='gid://shopify/InventoryItem/999';v.version+=1;db.commit()
    enable(monkeypatch);assert svc.prepare_one(r.sessions,r.remote)
    assert not stock.dispatch_one(r.sessions,r.remote) and not r.remote.mutations

def test_planned_failure_can_renew_only_without_attempt(purchase,monkeypatch):
    r=purchase;rid=receive(r,make_bill(r),20);enable(monkeypatch);svc.prepare_one(r.sessions,r.remote)
    r.remote.cap_error=True;assert stock.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:
        doc=db.get(InventoryDocument,db.query(Movement).filter_by(receipt_id=rid).one().document_id)
        assert doc.attempts==0 and doc.plan
        frozen=(doc.dispatch_key,doc.payload_hash,doc.approval_hash)
        svc.retry(db,db.get(User,r.uid),doc.id)
        assert (doc.dispatch_key,doc.payload_hash,doc.approval_hash)==frozen
    r.remote.cap_error=False;assert stock.dispatch_one(r.sessions,r.remote);assert len(r.remote.mutations)==1

def test_observation_difference_is_not_a_correction(purchase,monkeypatch):
    r=purchase;rid=receive(r,make_bill(r),20);dispatch(r,monkeypatch)
    for v in r.remote.values.values():v['on_hand']-=1;v['available']-=1
    with r.sessions() as db:
        docid=db.query(Movement).filter_by(receipt_id=rid).one().document_id
        result=svc.comparison(db,db.get(User,r.uid),docid,r.remote)
        assert result['different'] and result['automatic_correction'] is False and result['authoritative_crm_balance'] is False
    assert len(r.remote.mutations)==1

@pytest.fixture
def purchase_pg(pg,monkeypatch):
    from test_inventory_postgres import setup
    r=setup(pg,monkeypatch);monkeypatch.setattr(settings,'PURCHASING_ENABLED',True);monkeypatch.setattr(adapter,'client',r.remote)
    with r.sessions() as db:svc.put_directory(db,db.get(User,r.uid),'LOCATION',{'location_id':LOCATION,'type':'SHOP','enabled':True})
    return r

def test_postgres_concurrent_receipt_approval_and_dispatch(purchase_pg,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    r=purchase_pg;bid=make_bill(r);data={'client_key':uuid4().hex,'bill_id':bid,'delivery_reference':uuid4().hex,'location_id':LOCATION,'lines':[{'bill_line':0,'accepted':20}]}
    with r.sessions() as db:
        row=svc.create(db,db.get(User,r.uid),'RECEIPT',data);rid=row.id;svc.action(db,db.get(User,r.uid),rid,'submit',rid)
    barrier=Barrier(2)
    def approve(_):
        barrier.wait()
        with r.sessions() as db:return svc.action(db,db.get(User,r.uid),rid,'approve',rid).id
    with ThreadPoolExecutor(2) as pool:assert len(set(pool.map(approve,range(2))))==1
    enable(monkeypatch)
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(lambda _:svc.prepare_one(r.sessions,r.remote),range(2)))==1
    with ThreadPoolExecutor(2) as pool:assert sum(pool.map(lambda _:stock.dispatch_one(r.sessions,r.remote),range(2)))==1
    assert len(r.remote.mutations)==1
    with r.sessions() as db:assert db.query(Movement).count()==1

def test_postgres_over_receipt_race(purchase_pg):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    r=purchase_pg;bid=make_bill(r);ids=[]
    for _ in range(2):
        with r.sessions() as db:
            u=db.get(User,r.uid);row=svc.create(db,u,'RECEIPT',{'client_key':uuid4().hex,'bill_id':bid,'delivery_reference':uuid4().hex,'location_id':LOCATION,'lines':[{'bill_line':0,'accepted':12}]});ids.append(row.id);svc.action(db,u,row.id,'submit',row.id)
    barrier=Barrier(2)
    def approve(rid):
        barrier.wait()
        with r.sessions() as db:
            try:svc.action(db,db.get(User,r.uid),rid,'approve',rid);return 'ok'
            except HTTPException:db.rollback();return 'blocked'
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(approve,ids))==['blocked','ok']

def test_postgres_payment_race_and_frozen_history(purchase_pg):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    r=purchase_pg;bid=make_bill(r);barrier=Barrier(2)
    def pay(_):
        barrier.wait()
        with r.sessions() as db:
            try:svc.create(db,db.get(User,r.uid),'PAYMENT',{'client_key':uuid4().hex,'bill_id':bid,'amount':'1500.00','method':'BANK','verified':True,'date':date.today().isoformat(),'reference':uuid4().hex});return 'ok'
            except HTTPException:db.rollback();return 'blocked'
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(pay,range(2)))==['blocked','ok']
    with r.sessions() as db:
        with pytest.raises(Exception):db.execute(text("UPDATE purchase_records SET payload='{}' WHERE id=:id"),{'id':bid});db.commit()
        db.rollback();assert svc.public(db,svc.get(db,bid))['paid']=='1500.00'

def test_empty_payment_history_and_edit_freeze(purchase):
    r=purchase;data=bill_data(r);bid=make_bill(r,approved=False,data=data)
    with r.sessions() as db:
        user=db.get(User,r.uid);row=svc.get(db,bid);assert svc.public(db,row)['paid']=='0.00'
        data['lines'][0]['unit_cost']='90.00';data['version']=row.version;svc.edit_bill(db,user,bid,data)
        assert svc.public(db,row)['payload']['total']=='1800.00'
        with pytest.raises(HTTPException):svc.edit_bill(db,user,bid,data)
        svc.action(db,user,bid,'submit',bid)
        data['version']=row.version
        with pytest.raises(HTTPException):svc.edit_bill(db,user,bid,data)
        with pytest.raises(Exception):db.execute(text("UPDATE purchase_records SET payload='{}' WHERE id=:id"),{'id':bid});db.commit()

def test_delivery_reference_survives_lost_response_new_request_key(purchase):
    r=purchase;bid=make_bill(r);data={'client_key':uuid4().hex,'bill_id':bid,'delivery_reference':'DELIVERY-ONE','location_id':LOCATION,'lines':[{'bill_line':0,'accepted':8}]}
    with r.sessions() as db:
        u=db.get(User,r.uid);a=svc.create(db,u,'RECEIPT',data)
        assert svc.create(db,u,'RECEIPT',data).id==a.id
        with pytest.raises(HTTPException):svc.create(db,u,'RECEIPT',{**data,'client_key':uuid4().hex})

def test_restart_after_durable_intent_fences_without_replay(purchase,monkeypatch):
    from datetime import datetime,timedelta
    r=purchase;rid=receive(r,make_bill(r),20);enable(monkeypatch);assert svc.prepare_one(r.sessions,r.remote)
    with r.sessions() as db:
        doc=db.get(InventoryDocument,db.query(Movement).filter_by(receipt_id=rid).one().document_id)
        doc.status='DISPATCHING';doc.attempts=1;doc.dispatched_at=datetime.utcnow()-timedelta(minutes=3)
        stock.event(db,doc,'DISPATCH_INTENT',key=doc.dispatch_key,reference='urn:pixiepinks:inventory:'+doc.shop+':'+doc.id);db.commit()
    assert stock.dispatch_one(r.sessions,r.remote)
    assert not stock.dispatch_one(r.sessions,r.remote) and not r.remote.mutations
    with r.sessions() as db:
        assert svc.public(db,svc.get(db,rid))['movements'][0]['sync_status']=='Needs Review'

@pytest.mark.parametrize('field,value',[('quantity',0),('quantity',True),('unit_cost','-1'),('unit_cost','0.001'),('tax_rate','101')])
def test_invalid_purchase_amounts_never_create_intent(purchase,field,value):
    r=purchase;data=bill_data(r);data['lines'][0][field]=value
    with r.sessions() as db:
        with pytest.raises(HTTPException):svc.create(db,db.get(User,r.uid),'BILL',data)
        assert db.query(InventoryDocument).count()==0 and not r.remote.mutations

def test_payment_reference_cannot_be_recorded_twice_with_new_key(purchase):
    r=purchase;bid=make_bill(r);data={'client_key':uuid4().hex,'bill_id':bid,'amount':'500.00','method':'BANK','verified':True,'date':date.today().isoformat(),'reference':'BANK-ONE'}
    with r.sessions() as db:
        u=db.get(User,r.uid);svc.create(db,u,'PAYMENT',data)
        with pytest.raises(HTTPException):svc.create(db,u,'PAYMENT',{**data,'client_key':uuid4().hex})
        assert svc.public(db,svc.get(db,bid))['paid']=='500.00'

def test_context_verified_destination_names_and_inactive_filter(purchase):
    r=purchase;url='/crm/retail/api/purchases/context';v=r.client.get(url).json()
    assert {l['name'] for l in v['locations']}=={'Shop','Warehouse'} and all(l['verified_active'] for l in v['locations'])
    r.remote.locations=lambda:[{'id':LOCATION,'name':'Magammana West','isActive':False}]
    v=r.client.get(url).json();assert not any(l['verified_active'] for l in v['locations'])
    with r.sessions() as db:
        with pytest.raises(HTTPException):svc.create(db,db.get(User,r.uid),'BILL',bill_data(r))

def test_location_verification_failure_is_not_empty_success(purchase):
    r=purchase
    def unavailable():raise InventoryError('READ_UNAVAILABLE')
    r.remote.locations=unavailable
    v=r.client.get('/crm/retail/api/purchases/context').json();assert v['location_error'] and not any(l['verified_active'] for l in v['locations'])

def test_preview_reuses_exact_quote_without_persistence(purchase):
    r=purchase;d=bill_data(r);d['lines'][0].update(tax_rate='18');d['discount']={'type':'PERCENT','value':'5'}
    response=r.client.post('/crm/retail/api/purchases/preview',json=d,headers={'X-CSRF-Token':r.csrf});assert response.status_code==200
    q=response.json();assert q['subtotal']=='2000.00' and q['total']=='2242.00' and q['quantity_total']==20
    with r.sessions() as db:assert db.query(Record).filter_by(kind='BILL').count()==0 and db.query(InventoryDocument).count()==0
    assert not r.remote.mutations

def test_preview_permission_and_csrf(purchase):
    r=purchase;url='/crm/retail/api/purchases/preview'
    assert r.client.post(url,json=bill_data(r)).status_code==403
    with r.sessions() as db:
        u=db.get(User,r.uid);u.role='STAFF';u.section_access=['retail.purchases.view'];db.commit()
    assert r.client.post(url,json=bill_data(r),headers={'X-CSRF-Token':r.csrf}).status_code==403
    v=r.client.get('/crm/retail/api/purchases/context').json();assert not v['administrator']

def test_distinct_variants_with_shared_sku_not_collapsed(purchase):
    r=purchase;d=bill_data(r)
    with r.sessions() as db:
        for p in r.products[:2]:db.get(stock.RetailVariant,p['id']).sku='SUPPLIER-SHARED'
        db.commit();d['lines']=[{**d['lines'][0],'variant_id':p['id'],'version':p['version']} for p in r.products[:2]]
        q=svc.quote(db,d);assert len(q['lines'])==2 and q['total']=='4000.00'
        d['lines'][1]=dict(d['lines'][0])
        with pytest.raises(HTTPException):svc.quote(db,d)

def test_preview_does_not_repeat_shopify_location_queries(purchase):
    r=purchase;d=bill_data(r)
    def unavailable():raise InventoryError('READ_UNAVAILABLE')
    r.remote.locations=unavailable
    response=r.client.post('/crm/retail/api/purchases/preview',json=d,headers={'X-CSRF-Token':r.csrf})
    assert response.status_code==200  # Local mapped preview only; save must reverify.
    with r.sessions() as db:
        with pytest.raises(InventoryError):svc.create(db,db.get(User,r.uid),'BILL',d)

def test_destination_context_is_scoped_to_configured_shop(purchase):
    r=purchase
    with r.sessions() as db:
        db.add(Record(id=str(uuid4()),shop='different-shop',kind='LOCATION',client_key=uuid4().hex,business_key='location:foreign',request_hash='0'*64,payload={'location_id':'gid://shopify/Location/999','type':'SHOP','enabled':True},status='ACTIVE',actor_id=r.uid));db.commit()
    v=r.client.get('/crm/retail/api/purchases/context').json();assert len(v['locations'])==2
    assert all(l['payload']['location_id']!='gid://shopify/Location/999' for l in v['locations'])

def test_existing_snapshot_display_enrichment_never_rewrites_bill(purchase):
    import copy
    r=purchase;bid=make_bill(r,approved=False)
    with r.sessions() as db:
        row=svc.get(db,bid)
        row.payload={k:v for k,v in row.payload.items() if k not in {'quantity_total','subtotal','line_discount_total','invoice_discount','line_tax_total','taxable_amount'}};db.commit();before=copy.deepcopy(row.payload)
    response=r.client.get('/crm/retail/api/purchases/'+bid);assert response.json()['payload']['subtotal']=='2000.00'
    with r.sessions() as db:assert svc.get(db,bid).payload==before

def test_charge_tax_is_separate_from_product_tax(purchase):
    r=purchase;d=bill_data(r);d.update(charges='100',charge_tax='18')
    with r.sessions() as db:
        q=svc.quote(db,d);assert q['line_tax_total']=='0.00' and q['charge_tax']=='18.00' and q['tax_total']=='18.00' and q['total']=='2118.00'
