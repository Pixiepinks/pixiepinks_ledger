"""Retirement changes local preference only; Shopify and original allocations stay intact."""
from datetime import datetime,timedelta
from uuid import uuid4
import pytest
from fastapi import HTTPException
from models import User
from retail_models import *
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail,node,import_nodes,choose,allocate
from test_retail_postgres import pg
import retail_service as service
import retail_labels as labels
import retail_barcode_sync as sync

SUPPLIER='4792210130832'
@pytest.fixture
def retirement(retail):
    r=retail;import_nodes(r,[node(1,sku='4001946')]);allocate(r,choose(r,[1]));import_nodes(r,[node(1,sku='4001946',barcode=SUPPLIER)])
    with r.sessions() as db:
        row=db.get(RetailVariant,1);r.data={'version':row.version,'local_barcode':row.local_barcode,'shopify_barcode':row.barcode,'reason':'Supplier label physically checked; remove old local labels','labels_ack':True,'confirm':True}
    return r

def retire(r,data=None):return r.post('products/1/retire-local-barcode',data or r.data)

def test_retirement_preserves_ledger_and_uses_shopify_everywhere(retirement):
    r=retirement;response=retire(r);assert response.status_code==200,response.text
    assert response.json()['effective_barcode']==SUPPLIER and response.json()['shopify_write'] is False
    with r.sessions() as db:
        row=db.get(RetailVariant,1);assert row.local_barcode is None and row.barcode==SUPPLIER and row.sku=='4001946'
        assert db.query(RetailIdentifier).one().value==r.data['local_barcode']
        assert service.scan_page(db,r.data['local_barcode'])['total']==0
        assert service.scan_page(db,SUPPLIER)['total']==1
        assert sync.local_reason(db,row)=='NO_COMMITTED_LOCAL_BARCODE'
        assert 'BARCODE_RECONCILIATION' not in service.issues(row,service.audit_context(db))
        assert 'LOCAL_PENDING_SYNC' not in service.issues(row,service.audit_context(db))
        a=db.query(RetailAudit).filter_by(action='LOCAL_BARCODE_RETIRED').one();assert a.detail['allocation_id']==db.query(RetailIdentifier).one().id
        assert service.public(row,service.audit_context(db))['retired_local_barcodes']==[r.data['local_barcode']]
    token=choose(r,[1])
    with r.sessions() as db:
        rows,total=labels.print_rows(db,db.get(User,r.uid),token);assert rows[0]['barcode']==SUPPLIER
    assert retire(r).status_code==409  # No repeated mutation/audit.

def test_no_reuse_and_import_does_not_restore_retired_code(retirement):
    r=retirement;assert retire(r).status_code==200
    import_nodes(r,[node(1,sku='4001946',barcode=SUPPLIER),node(2,sku='OTHER')]);allocate(r,choose(r,[2]))
    with r.sessions() as db:
        assert db.get(RetailVariant,1).local_barcode is None
        assert db.get(RetailVariant,2).local_barcode!=r.data['local_barcode']
        assert db.query(RetailIdentifier).filter_by(value=r.data['local_barcode']).one().variant_id==1

@pytest.mark.parametrize('change',[{'labels_ack':False},{'confirm':False},{'reason':'short'},{'version':999},{'local_barcode':'P99'},{'shopify_barcode':'WRONG'}])
def test_exact_confirmation_and_version_required(retirement,change):
    r=retirement;assert retire(r,{**r.data,**change}).status_code in {409,422}
    with r.sessions() as db:assert db.get(RetailVariant,1).local_barcode==r.data['local_barcode']

def test_admin_csrf_and_shop_scope(retirement):
    r=retirement;assert r.client.post('/crm/retail/api/products/1/retire-local-barcode',json=r.data).status_code==403
    with r.sessions() as db:
        u=db.get(User,r.uid);u.role='STAFF';u.section_access=['retail.products.manage','retail.products.view'];db.commit()
    assert retire(r).status_code==403
    with r.sessions() as db:u=db.get(User,r.uid);u.role='ADMIN';db.commit();db.get(RetailVariant,1).shop='different-shop';db.commit()
    assert retire(r).status_code==409

@pytest.mark.parametrize('field,value',[('stale',True),('missing',True),('barcode',None),('product_status','ARCHIVED')])
def test_invalid_shopify_fallback_fails_closed(retirement,field,value):
    r=retirement
    with r.sessions() as db:setattr(db.get(RetailVariant,1),field,value);db.commit()
    assert retire(r).status_code==409

@pytest.mark.parametrize('state',['QUEUED','CHECKING','SENT','UNKNOWN','CONFLICT','PILOT_INTENT'])
def test_sync_jobs_block_retirement_without_changes(retirement,state):
    r=retirement
    with r.sessions() as db:
        job=RetailBarcodeJob(shop=service.shop(),actor_id=r.uid,actor='fixture',token=uuid4().hex,status='RUNNING',api_version='2026-10',digest='0'*64,expires_at=datetime.utcnow()+timedelta(minutes=10));db.add(job);db.flush()
        db.add(RetailBarcodeItem(job_id=job.id,variant_id=1,idempotency_key=uuid4().hex,snapshot={'code':r.data['local_barcode']},before={},status=state,reason='fixture'));db.commit()
    assert retire(r).status_code==409
    with r.sessions() as db:assert db.query(RetailBarcodeItem).one().status==state


def test_old_pdf_snapshots_cannot_be_reprinted(retirement):
    r=retirement
    snapshot={'id':1,'barcode':r.data['local_barcode'],'title':'Fixture','variant':'','sku':'4001946','price':'1.00','copies':1}
    with r.sessions() as db:
        job=RetailPrintJob(shop=service.shop(),actor_id=r.uid,status='GENERATED',rows=[snapshot],template={},total=1);db.add(job);db.commit();jid=job.id
    assert retire(r).status_code==200
    with r.sessions() as db:
        with pytest.raises(HTTPException):labels.ensure_reprint_safe(db,db.get(RetailPrintJob,jid))
    assert r.client.get(f'/crm/retail/api/labels/jobs/{jid}/pdf').status_code==409
    assert r.post(f'labels/jobs/{jid}/reprint',{'confirm':True}).status_code==409


def test_pending_pdf_generation_blocks_retirement(retirement):
    r=retirement
    with r.sessions() as db:db.add(RetailPrintJob(shop=service.shop(),actor_id=r.uid,status='QUEUED',rows=[{'id':1,'barcode':r.data['local_barcode']}],template={},total=1));db.commit()
    assert retire(r).status_code==409

def test_postgres_retirement_serializes_repeated_requests(pg):
    from concurrent.futures import ThreadPoolExecutor
    from test_retail_phase1 import Reader
    import retail_sync
    _,sessions,uid=pg
    with sessions() as db:
        job=retail_sync.queue(db,db.get(User,uid),'FULL');job.status='RUNNING';jid=job.id;db.commit()
    retail_sync.run_sync(sessions,jid,Reader([([node(1,sku='4001946',barcode=SUPPLIER)],False)]))
    with sessions() as db:
        row=db.query(RetailVariant).one();row.local_barcode='P14';row.version+=1;db.add(RetailIdentifier(shop=service.shop(),variant_id=row.id,kind='barcode',value='P14',actor='fixture'));db.commit();rid=row.id
        data={'version':row.version,'local_barcode':'P14','shopify_barcode':SUPPLIER,'reason':'Verified supplier barcode; old labels removed','labels_ack':True,'confirm':True}
    def call(_):
        with sessions() as db:
            try:service.retire_local_barcode(db,db.get(User,uid),rid,data);return 'ok'
            except HTTPException:db.rollback();return 'stale'
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(call,range(2)))==['ok','stale']
    with sessions() as db:
        assert db.query(RetailAudit).filter_by(action='LOCAL_BARCODE_RETIRED').count()==1
        assert db.query(RetailIdentifier).one().value=='P14' and db.get(RetailVariant,rid).barcode==SUPPLIER


def test_admin_browser_retirement(retirement):
    from pathlib import Path
    from playwright.sync_api import sync_playwright
    r=retirement;root=Path(__file__).parents[1]
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=b.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        def route(h):
            req=h.request;path=req.url.removeprefix('https://retire.test')
            if path.startswith('/static/'):
                f=root/path.split('?',1)[0].lstrip('/')
                if f.is_file():h.fulfill(path=str(f));return
            if path.startswith('/crm/retail/'):
                response=r.client.request(req.method,path,content=req.post_data,headers={'X-CSRF-Token':req.headers.get('x-csrf-token',''),'Content-Type':'application/json'})
                h.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            h.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://retire.test/crm/retail/barcodes')
        def dialog(d):
            if d.type=='confirm':d.accept()
            else:d.accept(r.data['local_barcode'] if d.message.startswith('Type the local') else r.data['reason'])
        page.on('dialog',dialog);page.get_by_role('button',name='Retire local / Use Shopify',exact=True).click();page.get_by_text('Retired: '+r.data['local_barcode'],exact=True).wait_for();assert not errors
        assert page.get_by_role('button',name='Retire local / Use Shopify',exact=True).count()==0
        b.close()

@pytest.mark.parametrize('state',['QUEUED','DELIVERED_TO_AGENT','UNKNOWN','SUBMITTED_TO_PRINTER'])
def test_outstanding_hardware_labels_block_retirement(retirement,state):
    from hardware_models import PrintDevice,HardwareJob
    from pos_models import POSRegister
    r=retirement
    with r.sessions() as db:
        register=POSRegister(shop=service.shop(),name='Local fixture register',location_id='gid://shopify/Location/1');db.add(register);db.flush()
        device=PrintDevice(id=str(uuid4()),shop=service.shop(),register_id=register.id,location_id=register.location_id,name='Fixture device',config={});db.add(device);db.flush()
        label=RetailPrintJob(shop=service.shop(),actor_id=r.uid,status='GENERATED',rows=[{'id':1,'barcode':r.data['local_barcode']}],template={},total=1);db.add(label);db.flush()
        job=HardwareJob(id=str(uuid4()),shop=service.shop(),device_id=device.id,register_id=register.id,location_id=register.location_id,device_version=1,business_key=uuid4().hex,kind='LABEL',queue='FIXTURE-NOT-A-PRINTER',media='LABEL',actor_id=r.uid,label_id=label.id,detail={},sha256='0'*64,size=0,status=state,expires_at=datetime.utcnow()+timedelta(minutes=10));db.add(job);db.commit();jid=job.id
    assert retire(r).status_code==409
    with r.sessions() as db:assert db.get(HardwareJob,jid).status==state and db.get(RetailVariant,1).local_barcode==r.data['local_barcode']
