"""Inventory workflow tests run with disposable DBs and fake Shopify, never live stock."""
from datetime import datetime, timedelta
from uuid import uuid4
import copy
import json
import pytest
import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, DatabaseError
from fastapi import HTTPException
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail, node, import_nodes
from test_retail_postgres import pg
from models import User, Supplier
from retail_models import RetailVariant
from inventory_models import InventoryDocument, InventoryEvent, InventoryObservation
from settings import settings
import inventory_service as service
import inventory_shopify as adapter
from retail_inventory import InventoryError

LOCATION='gid://shopify/Location/1'
DESTINATION='gid://shopify/Location/2'


class FakeInventory:
    def __init__(self):self.values={};self.calls=[];self.mutations=[];self.error=None;self.cap_error=False;self.locations_error=False;self.read_error=False
    def capabilities(self):
        if self.cap_error:raise InventoryError('MOCK_ACCESS_UNAVAILABLE')
        return {'read_inventory':True,'read_locations':True,'write_inventory':True,'mutation_shape_verified':True,'source_contract_verified':True}
    def locations(self):return [{'id':LOCATION,'name':'Shop','isActive':True},{'id':DESTINATION,'name':'Warehouse','isActive':True}]
    def observe(self,line):
        self.calls.append((line['inventory_item_id'],line['location_id']))
        if self.read_error:raise InventoryError('READ_UNAVAILABLE')
        return {**self.values.setdefault((line['inventory_item_id'],line['location_id']),{'on_hand':10,'available':8}),'location_name':'Shop'}
    def apply(self,plan,reference,key,kind,reason=None,prepared=None):
        self.mutations.append((copy.deepcopy(plan),reference,key))
        if self.error:raise self.error
        for p in plan:
            value=self.values[(p['inventory_item_id'],p['location_id'])]
            if kind in {'COUNT','OPENING'} and value['on_hand']!=p['before']:raise InventoryError('COMPARE_FAILED')
            if kind not in {'COUNT','OPENING'} and value['available']!=p['available_before']:raise InventoryError('CHANGE_FROM_QUANTITY_STALE')
            delta=p['after']-p['before'];value['on_hand']+=delta;value['available']+=delta
        return {'group_id':'gid://shopify/InventoryAdjustmentGroup/1','group':{'referenceDocumentUri':reference},'operation':'SET' if kind in {'COUNT','OPENING'} else 'ADJUST'}


@pytest.fixture
def inv(retail,monkeypatch):
    r=retail;monkeypatch.setattr(settings,'INVENTORY_ENABLED',True)
    import_nodes(r,[node(1,barcode='P1',sku='1001200'),node(2,barcode='SHARED'),node(3,barcode='SHARED')])
    remote=FakeInventory();monkeypatch.setattr(adapter,'client',remote)
    with r.sessions() as db:
        supplier=Supplier(name='Inventory supplier');db.add(supplier);db.commit();r.supplier=supplier.id
        r.products=[{'id':v.id,'version':v.version} for v in db.query(RetailVariant).order_by(RetailVariant.id)]
    r.remote=remote;return r


def payload(r,kind='RECEIPT',qty=2):
    return {'client_key':uuid4().hex,'kind':kind,'supplier_id':r.supplier,'invoice':'INV-SUPPLIER-'+uuid4().hex[:12],
        'date':datetime.utcnow().date().isoformat(),'notes':'Owner-approved operational transaction, offline fixture',
        'reason':'CORRECTION','count_cutoff_ack':True,'destination':DESTINATION,
        'lines':[{'variant_id':r.products[0]['id'],'version':r.products[0]['version'],'location_id':LOCATION,
            **({} if kind in {'COUNT','OPENING'} else {'quantity':qty}),**({'unit_cost':'250.00'} if kind=='RECEIPT' else {})}]}


def post(r,path,data):return r.client.post('/crm/retail/api/inventory/'+path,json=data,headers={'X-CSRF-Token':r.csrf})


def prepare(r,kind='RECEIPT',qty=2):
    response=post(r,'documents',payload(r,kind,qty));assert response.status_code==200,response.text;return response.json()


def submit_approve(r,doc):
    response=post(r,'documents/'+doc['id']+'/submit',{});assert response.status_code==200,response.text
    response=post(r,'documents/'+doc['id']+'/approve',{'confirmation':doc['reference']});assert response.status_code==200,response.text
    return response.json()


def enable_mock_dispatch(monkeypatch):
    monkeypatch.setattr(settings,'SHOPIFY_INVENTORY_WRITE_ENABLED',True);monkeypatch.setattr(adapter,'MUTATION_CONTRACT_VERIFIED',True)


def test_receipt_idempotency_approval_intent_and_readback(inv,monkeypatch):
    r=inv;p=payload(r);first=post(r,'documents',p);assert first.status_code==200,first.text;doc=first.json()
    assert post(r,'documents',p).json()['id']==doc['id']
    changed=copy.deepcopy(p);changed['lines'][0]['quantity']=9;assert post(r,'documents',changed).status_code==409
    approved=submit_approve(r,doc);assert approved['status']=='APPROVED' and approved['plan'][0]['before']==10 and approved['plan'][0]['after']==12
    assert service.dispatch_one(r.sessions,r.remote) is False and r.remote.mutations==[]
    enable_mock_dispatch(monkeypatch);assert service.dispatch_one(r.sessions,r.remote) is True
    with r.sessions() as db:
        record=db.get(InventoryDocument,doc['id']);assert record.status=='VERIFIED' and record.attempts==1
        actions=[e.action for e in db.query(InventoryEvent).filter_by(document_id=doc['id']).order_by(InventoryEvent.id)]
        assert actions.index('DISPATCH_INTENT')<actions.index('REMOTE_ACCEPTED')<actions.index('READBACK_VERIFIED')
    assert service.dispatch_one(r.sessions,r.remote) is False and len(r.remote.mutations)==1
    _,reference,key=r.remote.mutations[0];assert reference.endswith(doc['id']) and key


@pytest.mark.parametrize('kind,qty,after',[('ADJUSTMENT',-2,8),('RECEIPT',5,15)])
def test_delta_adjustments_preserve_reserved_stock(inv,monkeypatch,kind,qty,after):
    r=inv;doc=prepare(r,kind,qty);submit_approve(r,doc)
    # A concurrent order changes available but not on_hand: receiving adjusts by delta,
    # rather than restoring the old available quantity or deducting the order again.
    r.remote.values[('gid://shopify/InventoryItem/1',LOCATION)]['available']=3
    enable_mock_dispatch(monkeypatch);service.dispatch_one(r.sessions,r.remote)
    value=r.remote.values[('gid://shopify/InventoryItem/1',LOCATION)]
    assert value['on_hand']==after and value['available']==3+qty


def test_delta_receipt_preserves_concurrent_onhand_change(inv,monkeypatch):
    r=inv;doc=prepare(r);submit_approve(r,doc);r.remote.values[('gid://shopify/InventoryItem/1',LOCATION)]['on_hand']=11
    enable_mock_dispatch(monkeypatch);service.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:assert db.get(InventoryDocument,doc['id']).status=='VERIFIED'
    assert len(r.remote.mutations)==1 and r.remote.values[('gid://shopify/InventoryItem/1',LOCATION)]['on_hand']==13


def test_uncertain_blocks_next_document_and_never_blindly_retries(inv,monkeypatch):
    r=inv;first=prepare(r);second=prepare(r);submit_approve(r,first);submit_approve(r,second)
    r.remote.error=TimeoutError('secret remote token');enable_mock_dispatch(monkeypatch)
    assert service.dispatch_one(r.sessions,r.remote) is True
    assert service.dispatch_one(r.sessions,r.remote) is False and len(r.remote.mutations)==1
    detail=r.client.get('/crm/retail/api/inventory/documents/'+first['id']).json()
    assert detail['status']=='UNCERTAIN' and 'secret' not in json.dumps(detail)
    closed=post(r,'documents/'+first['id']+'/reconcile',{'confirmation':first['reference'],'evidence':'Shopify history checked; outcome recorded by administrator'})
    assert closed.status_code==200 and closed.json()['status']=='RECONCILED_MANUALLY'
    assert len(r.remote.mutations)==1


@pytest.mark.parametrize('kind',['COUNT','OPENING'])
def test_count_cutoff_variance_and_history_preserved(inv,monkeypatch,kind):
    r=inv;doc=prepare(r,kind);assert doc['baseline'][0]['on_hand']==10
    assert post(r,'documents/'+doc['id']+'/submit',{}).status_code==409
    assert post(r,'documents/'+doc['id']+'/count',{'line':0,'quantity':9}).status_code==200
    assert post(r,'documents/'+doc['id']+'/count',{'line':0,'quantity':11}).status_code==200
    approved=submit_approve(r,doc);assert approved['plan'][0]['after']==11
    enable_mock_dispatch(monkeypatch);service.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:
        assert db.get(InventoryDocument,doc['id']).status=='VERIFIED'
        assert db.query(InventoryObservation).filter_by(document_id=doc['id']).count()==2
    stock=r.client.get(f"/crm/retail/api/inventory/stock/{r.products[0]['id']}",params={'version':r.products[0]['version'],'location':LOCATION}).json()
    assert stock['available']==9 and stock['physical_balance']==('OPENING_RECONCILED' if kind=='OPENING' else 'PROVISIONAL')


def test_count_changed_during_observation_requires_restart(inv):
    r=inv;doc=prepare(r,'COUNT');post(r,'documents/'+doc['id']+'/count',{'line':0,'quantity':5})
    r.remote.values[('gid://shopify/InventoryItem/1',LOCATION)]['available']=7
    response=post(r,'documents/'+doc['id']+'/submit',{});assert response.status_code==409
    assert r.remote.mutations==[]


def test_native_transfer_pending_not_two_adjustments(inv,monkeypatch):
    r=inv;doc=prepare(r,'TRANSFER');approved=submit_approve(r,doc);assert approved['status']=='TRANSFER_PENDING'
    assert post(r,'documents/'+doc['id']+'/receive',{'confirmation':doc['reference']}).status_code==409
    for action in ['dispatch','receive']:assert post(r,'documents/'+doc['id']+'/'+action,{'confirmation':doc['reference']}).status_code==200
    enable_mock_dispatch(monkeypatch);assert service.dispatch_one(r.sessions,r.remote) is False and r.remote.mutations==[]
    with r.sessions() as db:assert db.get(InventoryDocument,doc['id']).status=='TRANSFER_PENDING'


def test_stock_reorder_reports_and_no_valuation(inv):
    r=inv;product=r.products[0]
    response=post(r,'policies',{'variant_id':product['id'],'version':product['version'],'location_id':LOCATION,'minimum':9,'target':20,'supplier_id':r.supplier});assert response.status_code==200,response.text
    report=post(r,'report',{'location':LOCATION,'products':[product]}).json()
    assert report['valuation'] is None and report['rows'][0]['low_stock'] is True and report['rows'][0]['suggested_replenishment']==12
    r.remote.read_error=True
    stock=r.client.get(f"/crm/retail/api/inventory/stock/{product['id']}",params={'version':product['version'],'location':LOCATION}).json()
    assert stock['status']=='UNAVAILABLE' and stock['available'] is None and stock['on_hand'] is None


def test_permissions_csrf_shop_and_admin_approval(inv):
    r=inv;assert r.client.get('/crm/retail/inventory').status_code==200
    assert r.client.post('/crm/retail/api/inventory/documents',json=payload(r)).status_code==403
    doc=prepare(r)
    with r.sessions() as db:
        user=db.get(User,r.uid);user.role='STAFF';user.section_access=['retail.inventory.view','retail.inventory.prepare','retail.inventory.manage'];db.commit()
    assert post(r,'documents/'+doc['id']+'/submit',{}).status_code==200
    assert post(r,'documents/'+doc['id']+'/approve',{'confirmation':doc['reference']}).status_code==403
    with r.sessions() as db:
        user=db.get(User,r.uid);user.section_access=['retail.inventory.view'];db.commit()
    assert post(r,'documents',payload(r)).status_code==403
    with r.sessions() as db:
        user=db.get(User,r.uid);user.role='ADMIN';db.commit()
        # Separate store cannot see this document or its movement records.
    old=settings.SHOPIFY_SHOP
    try:
        settings.SHOPIFY_SHOP='other-shop'
        assert r.client.get('/crm/retail/api/inventory/documents/'+doc['id']).status_code==404
        assert r.client.get('/crm/retail/api/inventory/documents').json()['total']==0
    finally:settings.SHOPIFY_SHOP=old


@pytest.mark.parametrize('field,value',[('quantity',0),('quantity',True),('unit_cost','NaN'),('unit_cost','-1'),('unit_cost','1.001'),('quantity',1000001)])
def test_invalid_receiving_data(inv,field,value):
    p=payload(inv);p['lines'][0][field]=value
    assert post(inv,'documents',p).status_code==422


def test_immutable_document_and_audit_fences(inv):
    r=inv;doc=prepare(r);submit_approve(r,doc)
    for sql in ["UPDATE inventory_documents SET reference='CHANGED' WHERE id=:id", "UPDATE inventory_documents SET plan='[]' WHERE id=:id", "DELETE FROM inventory_documents WHERE id=:id", "DELETE FROM inventory_events WHERE document_id=:id"]:
        with r.sessions() as db:
            with pytest.raises(DatabaseError):db.execute(text(sql),{'id':doc['id']});db.commit()


def test_stale_dispatch_becomes_uncertain_and_revoked_approval_blocks(inv,monkeypatch):
    r=inv;doc=prepare(r);submit_approve(r,doc);enable_mock_dispatch(monkeypatch)
    with r.sessions() as db:u=db.get(User,r.uid);u.is_active=False;db.commit()
    assert service.dispatch_one(r.sessions,r.remote) is True and r.remote.mutations==[]
    with r.sessions() as db:assert db.get(InventoryDocument,doc['id']).status=='CONFLICT'


def test_report_formula_escaping():
    assert "'=HYPERLINK" in service.csv_report([{'title':'=HYPERLINK("bad")'}])


def test_production_adapter_source_gate(monkeypatch):
    monkeypatch.setattr(settings,'SHOPIFY_INVENTORY_WRITE_ENABLED',True)
    monkeypatch.setattr(adapter,'MUTATION_CONTRACT_VERIFIED',False)
    client=adapter.InventoryShopify()
    with pytest.raises(InventoryError,match='INVENTORY_WRITES_BLOCKED'):client.request(adapter.SET,{},mutation=True)
    with pytest.raises(InventoryError,match='UNSUPPORTED_OPERATION'):client.request('mutation { inventoryAdjustQuantities }',{},mutation=True)


def test_receiving_supplier_invoice_cannot_be_dispatched_twice_under_new_key(inv):
    r=inv;p=payload(r);doc=post(r,'documents',p);assert doc.status_code==200
    p['client_key']=uuid4().hex
    assert post(r,'documents',p).status_code==409


def test_stale_worker_is_never_requeued(inv,monkeypatch):
    r=inv;doc=prepare(r);submit_approve(r,doc)
    with r.sessions() as db:
        record=db.get(InventoryDocument,doc['id']);record.status='DISPATCHING';record.attempts=1;record.dispatched_at=datetime.utcnow()-timedelta(minutes=3);db.commit()
    assert service.dispatch_one(r.sessions,r.remote) is True
    with r.sessions() as db:assert db.get(InventoryDocument,doc['id']).status=='UNCERTAIN'
    assert r.remote.mutations==[]


def test_accepted_mutation_bad_readback_requires_review_not_retry(inv,monkeypatch):
    r=inv;doc=prepare(r);submit_approve(r,doc);enable_mock_dispatch(monkeypatch)
    original=r.remote.apply
    def applied(*args,**kwargs):
        group=original(*args,**kwargs);r.remote.read_error=True;return group
    r.remote.apply=applied
    service.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:assert db.get(InventoryDocument,doc['id']).status=='APPLIED_REVIEW'
    assert service.dispatch_one(r.sessions,r.remote) is False and len(r.remote.mutations)==1


def test_inventory_only_staff_landing_and_feature_off(inv,monkeypatch):
    import staff_access
    r=inv
    with r.sessions() as db:
        user=db.get(User,r.uid);user.role='STAFF';user.section_access=['retail.inventory.view'];db.commit()
        assert staff_access.landing(user)=='/crm/retail/inventory'
        assert staff_access.section_for('/crm/retail/inventory')=='retail.inventory.view'
    assert r.client.get('/crm/retail/api/inventory/context').status_code==200
    monkeypatch.setattr(settings,'INVENTORY_ENABLED',False)
    assert r.client.get('/crm/retail/api/inventory/context').status_code in {403,404}


def test_changed_version_blocks_dispatch(inv,monkeypatch):
    r=inv;doc=prepare(r);submit_approve(r,doc);enable_mock_dispatch(monkeypatch)
    with r.sessions() as db:
        product=db.get(RetailVariant,r.products[0]['id']);product.version+=1;db.commit()
    service.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:assert db.get(InventoryDocument,doc['id']).status=='CONFLICT'
    assert r.remote.mutations==[]


def test_expired_approval_blocks_dispatch(inv,monkeypatch):
    r=inv;doc=prepare(r);submit_approve(r,doc);enable_mock_dispatch(monkeypatch)
    class Future(datetime):
        @classmethod
        def utcnow(cls):return datetime.utcnow()+timedelta(minutes=31)
    monkeypatch.setattr(service,'datetime',Future)
    service.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:
        record=db.get(InventoryDocument,doc['id']);assert record.status=='CONFLICT' and record.error=='APPROVAL_EXPIRED'
    assert r.remote.mutations==[]


def test_barcode_flags_cannot_enable_inventory_dispatch(inv,monkeypatch):
    r=inv;doc=prepare(r);submit_approve(r,doc)
    monkeypatch.setattr(settings,'SHOPIFY_PRODUCT_WRITE_ENABLED',True)
    monkeypatch.setattr(settings,'RETAIL_BARCODE_SYNC_ENABLED',True)
    assert settings.SHOPIFY_INVENTORY_WRITE_ENABLED is False
    assert service.dispatch_one(r.sessions,r.remote) is False and r.remote.mutations==[]


def test_count_changed_after_approval_stays_absolute_and_blocks(inv,monkeypatch):
    r=inv;doc=prepare(r,'COUNT');post(r,'documents/'+doc['id']+'/count',{'line':0,'quantity':12});submit_approve(r,doc)
    r.remote.values[('gid://shopify/InventoryItem/1',LOCATION)]['on_hand']=11
    enable_mock_dispatch(monkeypatch);service.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:assert db.get(InventoryDocument,doc['id']).status=='CONFLICT'
    assert r.remote.mutations==[]


def test_contract_identity_cannot_be_changed(inv):
    r=inv;doc=prepare(r)
    with r.sessions() as db:
        with pytest.raises(DatabaseError):db.execute(text("UPDATE inventory_documents SET contract_version='old' WHERE id=:id"),{'id':doc['id']});db.commit()


def test_unknown_contract_document_cannot_be_approved(inv):
    r=inv;p=payload(r)
    # Insert pre-release row as it would exist after additive migration, with NULL contract.
    with r.sessions() as db:
        source=service.create(db,db.get(User,r.uid),p,r.remote)
        service.submit(db,db.get(User,r.uid),source.id,r.remote)
        legacy=InventoryDocument(id=str(uuid4()),shop=source.shop,client_key=uuid4().hex,kind=source.kind,
            business_key=None,reference='LEGACY-'+uuid4().hex[:10],payload=source.payload,payload_hash=source.payload_hash,
            actor_id=r.uid,status='SUBMITTED',baseline=source.baseline,plan=source.plan,dispatch_key=str(uuid4()),contract_version=None)
        db.add(legacy);db.commit();document_id=legacy.id;reference=legacy.reference
    assert post(r,'documents/'+document_id+'/approve',{'confirmation':reference}).status_code==409
    assert r.remote.mutations==[]


def test_delta_execution_uses_fresh_baseline_without_rewriting_approval(inv,monkeypatch):
    r=inv;doc=prepare(r);submit_approve(r,doc)
    value=r.remote.values[('gid://shopify/InventoryItem/1',LOCATION)];value.update(on_hand=7,available=5)
    enable_mock_dispatch(monkeypatch);service.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:
        record=db.get(InventoryDocument,doc['id'])
        assert record.plan[0]['before']==10 and record.plan[0]['after']==12
        intent=db.query(InventoryEvent).filter_by(document_id=doc['id'],action='DISPATCH_INTENT').one()
        assert intent.detail['plan'][0]['before']==7 and intent.detail['plan'][0]['after']==9
        report=service.movement_report(db,db.get(User,r.uid))[0]
        assert report['approved_before']==10 and report['before']==7 and report['after']==9 and report['observed_on_hand']==9
        accepted=db.query(InventoryEvent).filter_by(document_id=doc['id'],action='REMOTE_ACCEPTED').one()
        assert accepted.detail['outcome']['group']['referenceDocumentUri'].endswith(doc['id'])


def test_delta_cas_race_is_uncertain_with_exact_saved_wire(inv,monkeypatch):
    r=inv;doc=prepare(r);submit_approve(r,doc);enable_mock_dispatch(monkeypatch)
    original=r.remote.apply
    def race(*args,**kwargs):
        r.remote.values[('gid://shopify/InventoryItem/1',LOCATION)]['available']-=1
        return original(*args,**kwargs)
    r.remote.apply=race
    service.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:
        record=db.get(InventoryDocument,doc['id']);assert record.status=='UNCERTAIN' and record.attempts==1
        intent=db.query(InventoryEvent).filter_by(document_id=doc['id'],action='DISPATCH_INTENT').one()
        assert intent.detail['wire']['variables']['key']==record.dispatch_key
        assert intent.detail['wire']['variables']['input']['changes'][0]['changeFromQuantity']==8
        assert intent.detail['wire_sha256']==service.digest(intent.detail['wire'])
    assert service.dispatch_one(r.sessions,r.remote) is False


def test_negative_adjustment_never_removes_committed_stock(inv,monkeypatch):
    r=inv;doc=prepare(r,'ADJUSTMENT',-9);submit_approve(r,doc);enable_mock_dispatch(monkeypatch)
    service.dispatch_one(r.sessions,r.remote)
    with r.sessions() as db:assert db.get(InventoryDocument,doc['id']).error=='INSUFFICIENT_AVAILABLE_STOCK'
    assert r.remote.mutations==[]
