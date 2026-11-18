"""Retail Phase 1 uses isolated databases, fake Shopify reads and offline decoders."""
import copy
from contextlib import closing
import io
from datetime import datetime,timedelta
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from models import User,GlobalAIReplyControl,WhatsAppConversationMessage
from retail_models import *
import retail_service as service
import retail_sync as sync
import retail_labels as labels
import main
from test_whatsapp_quick_messages import inbox


@pytest.fixture
def retail(inbox,tmp_path,monkeypatch):
    client,sessions,ids,sends,login,csrf=inbox
    monkeypatch.setattr(main.settings,'RETAIL_ENABLED',True)
    monkeypatch.setattr(main.settings,'SHOPIFY_SHOP','retail-fixture')
    monkeypatch.setattr(main.settings,'RETAIL_LABEL_DIR',str(tmp_path/'labels'))
    with sessions() as db:
        user=db.query(User).filter_by(username='quick-admin').one();uid=user.id
    def post(path,data):
        if path=='labels/jobs' and data.get('confirm') is True and 'preview_token' not in data:
            preview=client.post('/crm/retail/api/labels/preview',json={k:v for k,v in data.items() if k!='confirm'},headers={'X-CSRF-Token':csrf})
            if preview.status_code!=200:return preview
            data={**data,'preview_token':preview.headers['X-Retail-Preview']}
        return client.post('/crm/retail/api/'+path,json=data,headers={'X-CSRF-Token':csrf})
    return SimpleNamespace(client=client,sessions=sessions,uid=uid,post=post,csrf=csrf,login=login,sends=sends,root=tmp_path)


def node(i,**kw):
    now='2026-01-01T00:00:00Z'
    value={'id':f'gid://shopify/ProductVariant/{i}','title':'Black / 16 inches','sku':None,'barcode':None,'price':'8500.00','compareAtPrice':'9500.00',
        'updatedAt':now,'selectedOptions':[{'name':'Colour','value':'Black'}],'inventoryItem':{'id':f'gid://shopify/InventoryItem/{i}','tracked':True},
        'product':{'id':f'gid://shopify/Product/{i}','title':'Stand Fan '+str(i),'status':'ACTIVE','productType':'Fans','vendor':'Elevate',
            'updatedAt':now,'featuredImage':{'url':'https://cdn.shopify.com/test.jpg'},'collections':{'nodes':[{'id':'gid://shopify/Collection/1','title':'Fans'}],'pageInfo':{'hasNextPage':False}}}}
    value.update(kw);return value

class Reader:
    def __init__(self,pages):self.pages=iter(pages);self.calls=[]
    def read_page(self,cursor,query):
        self.calls.append((cursor,query));page=next(self.pages)
        if isinstance(page,Exception):raise page
        nodes,more=page
        return {'shop':{'currencyCode':'LKR'},'productVariants':{'nodes':nodes,'pageInfo':{'hasNextPage':more,'endCursor':str(len(self.calls))}}}


def import_nodes(r,nodes,mode='FULL',pages=None):
    reader=Reader(pages or [(nodes,False)])
    with r.sessions() as db:
        user=db.get(User,r.uid);job=sync.queue(db,user,mode);rid=job.id;job.status='RUNNING';db.commit()
    sync.run_sync(r.sessions,rid,reader)
    with r.sessions() as db:return db.get(RetailSyncJob,rid),reader


def choose(r,ids=None,filters=None):
    response=r.post('selection',{'ids':ids} if ids is not None else {'filters':filters or {}})
    assert response.status_code==200,response.text
    return response.json()['token']


def allocate(r,token,kind='barcode',**kw):
    response=r.post('allocations/preview',{'selection':token,'kind':kind,**kw});assert response.status_code==200,response.text
    response=r.post('allocations/commit',{'token':response.json()['token'],'confirm':True});assert response.status_code==200,response.text
    return response


def test_catalog_pagination_audit_preservation_and_atomic_failure(retail):
    r=retail;job,reader=import_nodes(r,[],pages=[([node(1,sku='SAME',barcode='1234567890123')],True),([node(2,sku='SAME',barcode='1234567890123'),node(3,barcode='\nBAD')],False)])
    assert job.status=='COMPLETE' and len(reader.calls)==2
    data=r.client.get('/crm/retail/api/audit').json()
    assert data['products']==3 and data['variants']==3 and data['duplicate_sku_groups']==1 and data['duplicate_barcode_groups']==1
    assert data['counts']['INVALID_BARCODE']==1
    with r.sessions() as db:
        row=db.query(RetailVariant).filter_by(variant_id=node(1)['id']).one();old_id=row.id
        assert row.barcode=='1234567890123' and row.sku=='SAME' and row.inventory_item_id
    job,_=import_nodes(r,[],pages=[([node(1,sku='CHANGED')],True),RuntimeError('private upstream body')])
    assert job.status=='FAILED' and 'private' not in job.error
    with r.sessions() as db:assert db.get(RetailVariant,old_id).sku=='SAME' and db.query(RetailVariant).count()==3
    job,_=import_nodes(r,[node(1,sku='NEW',barcode='1234567890123')])
    assert job.status=='COMPLETE'
    with r.sessions() as db:
        assert db.query(RetailVariant).count()==3 and db.get(RetailVariant,old_id).sku=='NEW'
        assert db.query(RetailVariant).filter_by(missing=True).count()==2


def test_incremental_updates_do_not_archive_other_variants(retail):
    r=retail;import_nodes(r,[node(1),node(2)])
    job,reader=import_nodes(r,[node(1,sku='OK')],mode='INCREMENTAL')
    assert job.status=='COMPLETE' and reader.calls[0][1].startswith('updated_at:>=')
    with r.sessions() as db:assert db.query(RetailVariant).filter_by(missing=True).count()==0


def test_local_assignments_are_unique_pinned_idempotent_and_never_write_shopify(retail):
    r=retail;import_nodes(r,[node(1),node(2),node(3,sku='ORIGINAL',barcode='EXT-ABC')])
    token=choose(r,filters={})
    preview=r.post('allocations/preview',{'selection':token,'kind':'barcode'}).json()
    assert preview['count']==2 and preview['rows'][0]['value']=='P1'
    with r.sessions() as db:assert db.query(RetailIdentifier).count()==0
    assert r.post('allocations/commit',{'token':preview['token'],'confirm':False}).status_code==422
    for _ in range(2):assert r.post('allocations/commit',{'token':preview['token'],'confirm':True}).status_code==200
    allocate(r,token,'sku',category='OTH')
    with r.sessions() as db:
        rows=db.query(RetailVariant).order_by(RetailVariant.id).all()
        assert [x.local_barcode for x in rows]==['P1','P2',None]
        assert rows[2].sku=='ORIGINAL' and rows[2].barcode=='EXT-ABC'
        assert db.query(RetailIdentifier).count()==4
    import_nodes(r,[node(1),node(2),node(3,sku='ORIGINAL',barcode='EXT-ABC')])
    with r.sessions() as db:assert db.query(RetailVariant).filter_by(local_barcode='P1').count()==1
    assert r.client.get('/crm/retail/api/products?issue=LOCAL_PENDING_SYNC').json()['total']==2
    assert not r.sends


def test_preview_cancel_stale_and_alternative_existing_barcode(retail):
    r=retail;import_nodes(r,[node(1),node(2,barcode='MANUFACTURER')]);token=choose(r,filters={})
    preview=r.post('allocations/preview',{'selection':token,'kind':'barcode'}).json()
    # Another allocation wins; the old preview cannot silently receive different values.
    allocate(r,choose(r,[2]),reason='Approved separate internal shelf identifier')
    assert r.post('allocations/commit',{'token':preview['token'],'confirm':True}).status_code==409
    allocate(r,choose(r,[1]))
    with r.sessions() as db:
        assert db.get(RetailVariant,2).barcode=='MANUFACTURER' and db.get(RetailVariant,2).barcode_alternative_reason
        assert {x.value for x in db.query(RetailIdentifier)}=={'P1','P2'}


def test_concurrent_allocation_retries_never_duplicate(retail):
    r=retail;import_nodes(r,[node(1),node(2)])
    def commit_one(rid):
        with r.sessions() as db:
            user=db.get(User,r.uid);t=service.selection(db,user,{'ids':[rid]})['token'];db.commit()
        with r.sessions() as db:
            p=service.allocate_preview(db,db.get(User,r.uid),t,'barcode');db.commit()
        with r.sessions() as db:
            try:service.allocate_commit(db,db.get(User,r.uid),p['token'],True);db.commit();return True
            except HTTPException:db.rollback();return False
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(commit_one,[1,2]))
    assert any(results)
    for rid in [1,2]:
        with r.sessions() as db:missing=not db.get(RetailVariant,rid).local_barcode
        if missing:allocate(r,choose(r,[rid]))
    with r.sessions() as db:assert {x.value for x in db.query(RetailIdentifier)}=={'P1','P2'}


def test_label_presets_decode_at_203_dpi_and_real_dimensions():
    import pypdfium2 as pdfium
    import zxingcpp
    from pypdf import PdfReader
    for name,config in labels.PRESETS.items():
        raw=labels.backend.render([labels.Label('P1','Stand Fan','Black','PP-OTH-000001','8500.00')],config)
        page=PdfReader(io.BytesIO(raw)).pages[0]
        assert float(page.mediabox.width)*25.4/72==pytest.approx(config['width_mm'],abs=.01)
        assert float(page.mediabox.height)*25.4/72==pytest.approx(config['height_mm'],abs=.01)
        with closing(pdfium.PdfDocument(raw)) as doc:
            image=doc[0].render(scale=203/72).to_pil();codes=zxingcpp.read_barcodes(image)
            assert len(codes)==1 and codes[0].text=='P1' and codes[0].format==zxingcpp.BarcodeFormat.Code128


@pytest.mark.parametrize('value',['1234567890123','ABC-123','PP-BIC-000123','p lower 123'])
def test_code128_exact_roundtrip_and_sinhala(value):
    import pypdfium2 as pdfium
    import zxingcpp
    config=labels.preset(60,40)
    raw=labels.backend.render([labels.Label(value,'ළමා බයිසිකලය සහ Stand Fan — very long name'*4,'නිල් / Blue','KEEP-SKU','39500.00')],config)
    with closing(pdfium.PdfDocument(raw)) as doc:
        codes=zxingcpp.read_barcodes(doc[0].render(scale=203/72).to_pil());assert codes[0].text==value


def test_oversize_identifier_not_shortened_or_shrunk():
    with pytest.raises(HTTPException,match='') as exc:labels.backend.render([labels.Label('LONG-EXISTING-BARCODE-123456789','Fan')],labels.preset(25,15))
    assert 'larger label' in exc.value.detail
    config=labels.preset();config['module_dots']=1
    with pytest.raises(HTTPException):labels.validate_config(config)


def test_bulk_pdf_jobs_snapshot_copies_download_print_confirmation_reprint(retail):
    r=retail;import_nodes(r,[node(1,barcode='P1'),node(2,barcode='P2')]);token=choose(r,filters={})
    p={'selection':token,'profile':'50x30','copies':{'1':2,'2':3},'confirm':True}
    response=r.post('labels/jobs',p);assert response.status_code==200,response.text
    rid=response.json()['id'];assert response.json()['total']==5
    assert r.client.get(f'/crm/retail/api/labels/jobs/{rid}/pdf').status_code==409
    assert labels.dispatch_one(r.sessions)
    pdf=r.client.get(f'/crm/retail/api/labels/jobs/{rid}/pdf');assert pdf.status_code==200
    from pypdf import PdfReader
    assert len(PdfReader(io.BytesIO(pdf.content)).pages)==5
    with r.sessions() as db:
        job=db.get(RetailPrintJob,rid);assert job.downloaded_at and not job.printed_at
    assert r.post(f'labels/jobs/{rid}/printed',{'confirm':True}).status_code==200
    assert r.post(f'labels/jobs/{rid}/reprint',{'confirm':True}).status_code==200
    with r.sessions() as db:
        original=db.get(RetailPrintJob,rid);reprint=db.query(RetailPrintJob).filter_by(parent_id=rid).one();assert reprint.rows==original.rows
    assert not r.sends


def test_duplicate_invalid_and_cross_shop_labels_blocked(retail):
    r=retail;import_nodes(r,[node(1,barcode='DUP'),node(2,barcode='DUP')])
    token=choose(r,filters={});assert r.post('labels/jobs',{'selection':token,'profile':'50x30','confirm':True}).status_code==409
    with r.sessions() as db:
        row=RetailVariant(shop='another-shop',**{**sync.normalize(node(99),'LKR'),'shopify_updated_at':datetime(2026,1,1)});db.add(row);db.commit();other=row.id
    assert r.post('selection',{'ids':[other]}).status_code==404


def test_permissions_csrf_feature_gate_and_csv_injection(retail,monkeypatch):
    r=retail;import_nodes(r,[node(1,sku='=SUM(A1)',barcode='P1')]);export=r.client.get('/crm/retail/api/export.csv');assert "'=SUM" in export.text
    assert r.client.post('/crm/retail/api/selection',json={'ids':[1]}).status_code==403
    r.login('quick-staff');assert r.client.get('/crm/retail/api/products').status_code==403
    with r.sessions() as db:
        user=db.query(User).filter_by(username='quick-staff').one();user.section_access=['retail.products.view'];db.commit()
    assert r.client.get('/crm/retail/api/products').status_code==200
    assert r.client.get('/crm/retail/api/audit').status_code==403
    assert r.post('sync',{'mode':'FULL','confirm':True}).status_code==403
    monkeypatch.setattr(main.settings,'RETAIL_ENABLED',False)
    assert r.client.get('/crm/retail/api/products').status_code in {403,404}
    assert main.settings.SHOPIFY_PRODUCT_WRITE_ENABLED is False


def test_profile_validation_a4_and_no_auto_printer():
    config=labels.preset(50,30);config.update(layout='a4',labels_per_row=4)
    from pypdf import PdfReader
    raw=labels.backend.render([labels.Label('P1','Product') for _ in range(40)],config)
    reader=PdfReader(io.BytesIO(raw));assert len(reader.pages)==2
    assert float(reader.pages[0].mediabox.width)*25.4/72==pytest.approx(210,abs=.01)


def test_large_catalog_paging_and_1500_label_batch(retail):
    r=retail;pages=[([node(i,barcode='P'+service.base36(i)) for i in range(first,first+50)],first<1451) for first in range(1,1501,50)]
    job,reader=import_nodes(r,[],pages=pages);assert job.status=='COMPLETE' and job.counts['variants']==1500 and len(reader.calls)==30
    response=r.client.get('/crm/retail/api/products?offset=1450&limit=50').json();assert response['total']==1500 and len(response['rows'])==50
    token=choose(r,filters={})
    response=r.post('labels/jobs',{'selection':token,'profile':'50x30','confirm':True});assert response.status_code==200,response.text
    assert labels.dispatch_one(r.sessions)
    with r.sessions() as db:
        job=db.get(RetailPrintJob,response.json()['id']);assert job.status=='GENERATED' and job.total==1500
        from pypdf import PdfReader
        assert len(PdfReader(io.BytesIO(labels.read_pdf(job))).pages)==1500


def test_sync_rate_limit_retry_uses_only_fixed_read_query(monkeypatch):
    import httpx
    calls=[];delays=[]
    data=Reader([([node(1)],False)]).read_page(None,None)
    class HTTP:
        @staticmethod
        def post(url,**kw):
            calls.append(kw)
            if len(calls)==1:return httpx.Response(429,headers={'Retry-After':'3'})
            return httpx.Response(200,json={'data':data})
    client=sync.RetailCatalogClient(http_client=HTTP)
    monkeypatch.setattr(main.settings,'SHOPIFY_SHOP','fixture')
    monkeypatch.setattr(client,'get_token',lambda **kw:'test-token-not-real')
    assert client.read_page(None,None,sleep=delays.append)==data
    assert delays==[3] and len(calls)==2
    assert all(call['json']['query']==sync.QUERY and 'mutation' not in call['json']['query'] for call in calls)


def test_full_sync_lock_deleted_archived_changes_and_existing_local_conflict(retail):
    r=retail;import_nodes(r,[node(1),node(2)])
    allocate(r,choose(r,[1]))
    value=node(1,barcode='OTHER');value['product']['status']='ARCHIVED';value['updatedAt']='2099-01-01T00:00:00Z'
    job,_=import_nodes(r,[value]);assert job.status=='COMPLETE'
    with r.sessions() as db:
        row=db.get(RetailVariant,1);assert row.local_barcode=='P1' and row.barcode=='OTHER' and row.stale and row.product_status=='ARCHIVED'
        assert db.get(RetailVariant,2).missing
        user=db.get(User,r.uid);queued=sync.queue(db,user,'FULL');db.commit()
    with r.sessions() as db:
        with pytest.raises(HTTPException):sync.queue(db,db.get(User,r.uid),'FULL')


def test_documented_shared_identifier_is_explicit_and_audited(retail):
    r=retail;import_nodes(r,[node(1,barcode='SHARED'),node(2,barcode='SHARED')])
    assert r.post('exceptions',{'kind':'barcode','value':'SHARED','ids':[1,2],'reason':'Approved same physical scan group','confirm':True}).status_code==200
    assert r.client.get('/crm/retail/api/products?issue=SHARED_BARCODE').json()['total']==2
    # Scan conflicts require separate explicit approval of the effective scan group.
    assert r.post('exceptions',{'kind':'effective','value':'SHARED','ids':[1,2],'reason':'Shared physical scan group approved','confirm':True}).status_code==200
    response=r.post('labels/jobs',{'selection':choose(r,filters={}), 'profile':'50x30','confirm':True});assert response.status_code==200,response.text


def test_pdf_failure_is_visible_and_retained_for_reprint(retail,monkeypatch):
    r=retail;import_nodes(r,[node(1,barcode='P1')]);response=r.post('labels/jobs',{'selection':choose(r,[1]),'profile':'50x30','confirm':True});rid=response.json()['id']
    def failure(*a,**kw):raise RuntimeError('secret filesystem detail')
    monkeypatch.setattr(labels.backend,'render',failure)
    assert labels.dispatch_one(r.sessions)
    with r.sessions() as db:
        job=db.get(RetailPrintJob,rid);assert job.status=='FAILED' and job.rows and 'secret' not in job.error


def test_browser_allocation_label_profile_preview_and_job_history(retail):
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    r=retail;import_nodes(r,[node(1),node(2,barcode='P2')])
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        # Keep native PDF viewer/dialog timing out of repeatable headless assertions.
        page.add_init_script('window.confirm = () => true')
        def serve(route):
            req=route.request;url=urlsplit(req.url)
            if url.hostname!='retail.test':route.fulfill(status=200,body='');return
            response=r.client.request(req.method,url.path+('?'+url.query if url.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token'}},follow_redirects=False)
            route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
        page.route('**/*',serve);page.goto('https://retail.test/crm/retail/barcodes')
        page.locator('#product-rows tr').nth(1).wait_for()
        page.get_by_role('button',name='Select all filtered results',exact=True).click()
        page.locator('#selection-count').get_by_text('2 variants in a frozen selection.',exact=False).wait_for()
        page.locator('#allocation > summary').click()
        page.get_by_role('button',name='Preview allocation',exact=True).click();page.locator('#allocation-confirm').wait_for()
        assert 'P1' in page.locator('#allocation-result').inner_text()
        page.get_by_role('button',name='Confirm local assignments',exact=True).click()
        page.locator('#allocation-result').get_by_text('committed locally',exact=False).wait_for()
        page.get_by_role('link',name='Print Selected',exact=True).click()
        page.locator('#label-selection-rows tr').nth(1).wait_for()
        page.locator('#label-profile').select_option('30x15')
        page.locator('summary').click()
        page.get_by_label('Label width (mm)',exact=True).fill('30')
        page.get_by_label('Label width (mm)',exact=True).dispatch_event('change')
        page.locator('#profile-title').fill('DBL 365B test profile')
        page.get_by_role('button',name='Save as new profile',exact=True).click()
        page.wait_for_function('document.querySelector("#label-profile").selectedOptions[0].textContent==="DBL 365B test profile"')
        page.get_by_label('Copies for variant 1',exact=True).fill('2')
        page.get_by_role('button',name='Preview first 5 labels',exact=True).click();page.locator('#label-preview').wait_for()
        page.get_by_role('button',name='Generate PDF batch',exact=True).click()
        page.locator('#print-rows').get_by_text('3 · QUEUED',exact=False).wait_for()
        assert labels.dispatch_one(r.sessions)
        page.get_by_role('button',name='Newest jobs',exact=True).click()
        page.locator('#print-rows').get_by_text('3 · GENERATED',exact=False).wait_for()
        assert 'Physical printing not confirmed' in page.locator('#print-rows').inner_text()
        for width in [768,1024]:
            page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path='/tmp/retail-label-ui.png',full_page=True)
        assert not errors and not r.sends
        browser.close()


def test_additive_migration_preserves_crm_and_duplicate_import_values(retail,monkeypatch):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    import database
    r=retail;import_nodes(r,[node(1,barcode='DUP'),node(2,barcode='DUP')])
    with r.sessions() as db:
        users=db.query(User).count();variants=db.query(RetailVariant).count()
    monkeypatch.setattr(database,'engine',r.sessions.kw['bind'])
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));command.stamp(config,'20261111_41');command.upgrade(config,'head');command.upgrade(config,'head')
    with r.sessions() as db:
        assert db.query(User).count()==users and db.query(RetailVariant).count()==variants
        assert db.query(RetailVariant).filter_by(barcode='DUP').count()==2


@pytest.mark.parametrize('path,payload',[('allocations/preview',{'selection':'x','kind':[]}),('sync',{'mode':[],'confirm':True}),('selection',{'filters':{'category':[]}}),('selection',{'filters':{'issue':[]}}),('selection',{'ids':[True]})])
def test_malformed_fields_fail_closed(retail,path,payload):
    assert retail.post(path,payload).status_code==422


def test_assignment_tombstones_and_audits_are_database_append_only(retail):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    r=retail;import_nodes(r,[node(1)]);allocate(r,choose(r,[1]))
    for table in ['retail_identifiers','retail_audits']:
        with r.sessions() as db:
            with pytest.raises(DBAPIError):db.execute(text(f'DELETE FROM {table}'))
            db.rollback()
    with r.sessions() as db:assert db.query(RetailIdentifier).count()==1 and db.query(RetailAudit).count()>0


def test_retail_grants_require_views_and_do_not_grant_existing_crm(retail):
    from fastapi.testclient import TestClient
    import staff_access
    r=retail;r.login('quick-staff')
    with r.sessions() as db:
        user=db.query(User).filter_by(username='quick-staff').one();user.section_access=['retail.barcodes.assign'];db.commit()
        assert staff_access.landing(user)=='/staff-home'
    assert r.client.get('/crm/retail/products').status_code==403
    with r.sessions() as db:
        user=db.query(User).filter_by(username='quick-staff').one();user.section_access=['retail.products.view','retail.barcodes.assign'];db.commit()
        assert staff_access.landing(user)=='/crm/retail/products'
    assert r.client.get('/crm/retail/products').status_code==200
    assert r.client.get('/crm/whatsapp').status_code==403
    anon=TestClient(main.app,base_url='https://testserver')
    assert anon.get('/crm/retail/api/products',follow_redirects=False).status_code==303


def test_human_readable_identifiers_and_price_are_not_shortened_by_name_limit():
    from pypdf import PdfReader
    config=labels.preset(60,40);config.update(max_name_chars=5,show_price=True)
    raw=labels.backend.render([labels.Label('EXACT-001','Product name can shorten','Black','PP-BIC-000123','39500.00')],config)
    text=PdfReader(io.BytesIO(raw)).pages[0].extract_text()
    assert 'EXACT-001' in text and 'PP-BIC-000123' in text and '39500.00' in text and 'PIXIEPINKS' in text


def test_multiple_variants_missing_options_and_missing_image(retail):
    a=node(1);b=node(2,selectedOptions=[],image=None);b['product']=copy.deepcopy(a['product']);b['product']['featuredImage']=None
    r=retail;job,_=import_nodes(r,[a,b]);assert job.status=='COMPLETE'
    rows=r.client.get('/crm/retail/api/products').json()['rows']
    assert all('MULTIPLE_VARIANTS' in row['issues'] for row in rows)
    assert 'MISSING_OPTIONS' in rows[1]['issues'] and rows[1]['image'] is None


def test_failed_allocation_transaction_keeps_counter_and_master_unchanged(retail):
    r=retail;import_nodes(r,[node(1)]);token=choose(r,[1]);preview=r.post('allocations/preview',{'selection':token,'kind':'barcode'}).json()
    with r.sessions() as db:
        service.allocate_commit(db,db.get(User,r.uid),preview['token'],True)
        db.rollback()
    with r.sessions() as db:
        assert db.query(RetailIdentifier).count()==0 and db.get(RetailVariant,1).local_barcode is None
        assert db.get(RetailCounter,(service.shop(),'barcode')).value==0
    assert r.post('allocations/commit',{'token':preview['token'],'confirm':True}).status_code==200


def test_reprint_blocks_changed_current_scan_identifier(retail):
    r=retail;import_nodes(r,[node(1,barcode='P1')]);rid=r.post('labels/jobs',{'selection':choose(r,[1]),'profile':'50x30','confirm':True}).json()['id']
    import_nodes(r,[node(1,barcode='CHANGED')])
    assert r.post(f'labels/jobs/{rid}/reprint',{'confirm':True}).status_code==409


def test_abandoned_jobs_fail_without_automatic_read_or_print(retail,monkeypatch):
    r=retail;old=datetime.utcnow()-timedelta(minutes=20)
    with r.sessions() as db:
        job=sync.queue(db,db.get(User,r.uid),'FULL');job.status='RUNNING';job.heartbeat=old;rid=job.id
        printing=RetailPrintJob(shop=service.shop(),actor_id=r.uid,status='GENERATING',claimed_at=old,template=labels.preset(),rows=[],total=1);db.add(printing);db.commit();pid=printing.id
    def forbidden(*a,**kw):raise AssertionError('No abandoned job may restart automatically')
    monkeypatch.setattr(sync.client,'read_page',forbidden);monkeypatch.setattr(labels.backend,'render',forbidden)
    assert not sync.dispatch_one(r.sessions) and not labels.dispatch_one(r.sessions)
    with r.sessions() as db:assert db.get(RetailSyncJob,rid).status=='FAILED' and db.get(RetailPrintJob,pid).status=='FAILED' and db.get(RetailShop,service.shop()).sync_job_id is None


def test_print_confirmation_rejects_changed_data_after_preview(retail):
    r=retail;import_nodes(r,[node(1,barcode='P1')]);token=choose(r,[1]);p={'selection':token,'profile':'50x30'}
    response=r.post('labels/preview',p);assert response.status_code==200
    preview=response.headers['X-Retail-Preview']
    import_nodes(r,[node(1,barcode='P1',price='9900.00')])
    assert r.post('labels/jobs',{**p,'preview_token':preview,'confirm':True}).status_code==409
    assert r.client.post('/crm/retail/api/labels/jobs',json={**p,'confirm':True},headers={'X-CSRF-Token':r.csrf}).status_code==422


def test_allocation_preview_values_can_be_paginated_without_assignment(retail):
    r=retail;import_nodes(r,[node(1),node(2),node(3)])
    response=r.post('allocations/preview',{'selection':choose(r,filters={}), 'kind':'barcode'});assert response.status_code==200
    token=response.json()['token'];page=r.client.get(f'/crm/retail/api/allocations/{token}?offset=1&limit=1').json()
    assert page['count']==3 and len(page['rows'])==1 and page['rows'][0]['value']=='P2'
    with r.sessions() as db:assert db.query(RetailIdentifier).count()==0


def test_revoked_shared_identifiers_block_old_document_download(retail):
    r=retail;import_nodes(r,[node(1,barcode='SHARED'),node(2,barcode='SHARED')])
    assert r.post('exceptions',{'kind':'effective','value':'SHARED','ids':[1,2],'reason':'Shared physical scan group approved','confirm':True}).status_code==200
    rid=r.post('labels/jobs',{'selection':choose(r,filters={}), 'profile':'50x30','confirm':True}).json()['id']
    assert labels.dispatch_one(r.sessions)
    assert r.post('exceptions/revoke',{'kind':'effective','value':'SHARED','reason':'Separate variants must not share identifiers','confirm':True}).status_code==200
    assert r.client.get(f'/crm/retail/api/labels/jobs/{rid}/pdf').status_code==409
    with r.sessions() as db:assert db.query(RetailException).count()==2
