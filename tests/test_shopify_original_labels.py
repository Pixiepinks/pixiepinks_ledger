"""Original identifiers, vector output and preview/profile behavior; no live printer/API."""
import io,copy
from contextlib import closing
import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from pypdf.generic import ContentStream
import pypdfium2 as pdfium
import zxingcpp
from models import User
from retail_models import RetailVariant,RetailIdentifier,RetailPrintJob
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail,node,import_nodes,choose,allocate
import retail_labels as labels


def c():return copy.deepcopy(labels.PRESETS[labels.SHOPIFY_COMPACT])
def item(code='AB12',**kw):return labels.Label(barcode=code,title='Atlas Binder Glue Pen — Long product description',sku='4001946',price='140.00',**kw)

def test_branded_layout_dimensions_content_and_exact_decode():
    raw=labels.backend.render([item()],c());reader=PdfReader(io.BytesIO(raw));page=reader.pages[0];lines=page.extract_text()
    for value in ['PIXIEPINKS','AB12','SKU: 4001946','Rs. 140.00']:assert value in lines
    assert float(page.mediabox.width)*25.4/72==pytest.approx(30,abs=.01) and float(page.mediabox.height)*25.4/72==pytest.approx(15,abs=.01)
    bars=[[float(v)*25.4/72 for v in operands] for operands,op in ContentStream(page.get_contents(),reader).operations if op==b're']
    assert all(abs(h)==pytest.approx(5,abs=.01) and w/labels.DOT>=1.98 for x,y,w,h in bars)
    with closing(pdfium.PdfDocument(raw)) as doc:
        codes=zxingcpp.read_barcodes(doc[0].render(scale=203/72).to_pil());assert len(codes)==1 and codes[0].text=='AB12'

@pytest.mark.parametrize('code',['4792210130832','012345678901','0000123456789'])
def test_long_original_cannot_fit_30mm_and_decodes_on_larger_profile(code):
    with pytest.raises(HTTPException,match=''):labels.backend.render([item(code)],c())
    big=copy.deepcopy(labels.PRESETS['60x40']);big['show_price']=True
    raw=labels.backend.render([item(code)],big)
    with closing(pdfium.PdfDocument(raw)) as doc:
        found=zxingcpp.read_barcodes(doc[0].render(scale=203/72).to_pil());assert len(found)==1 and found[0].text==code

@pytest.mark.parametrize('code',['000012','AB12','P14'])
@pytest.mark.parametrize('price',['350.00','1860.00','15500.00','125000.00'])
def test_short_codes_and_varied_prices_have_no_clipping(code,price):
    v=labels.Label(barcode=code,title='Product title '*30,sku='4001946',price=price)
    raw=labels.backend.render([v],c());page=PdfReader(io.BytesIO(raw)).pages[0]
    assert f'Rs. {float(price):,.2f}' in page.extract_text() and code in page.extract_text()
    positions=[]
    page.extract_text(visitor_text=lambda text,cm,tm,font,size:positions.append((text.strip(),size,tm[5]*25.4/72)) if text.strip() else None)
    assert all(size>=4.5 and 0<y<15 for _,size,y in positions)
    with closing(pdfium.PdfDocument(raw)) as doc:assert zxingcpp.read_barcodes(doc[0].render(scale=203/72).to_pil())[0].text==code

@pytest.mark.parametrize('sku,price',[('X'*100,'140.00'),('4001946',''),('4001946','NaN'),('4001946','-1')])
def test_unfit_fields_fail_without_truncation(sku,price):
    with pytest.raises(HTTPException):labels.backend.render([labels.Label(barcode='AB12',title='Test',sku=sku,price=price)],c())

def test_original_source_overrides_local_only_for_explicit_label_mode(retail):
    r=retail;import_nodes(r,[node(1,sku='4001946')]);allocate(r,choose(r,[1]));import_nodes(r,[node(1,sku='4001946',barcode='AB12',price='140.00')]);token=choose(r,[1])
    with r.sessions() as db:
        row=db.get(RetailVariant,1);before=(row.local_barcode,row.barcode,row.sku,row.price,row.version)
        original,count=labels.print_rows(db,db.get(User,r.uid),token,barcode_source='shopify');assert original[0]['barcode']=='AB12'
        assert (row.local_barcode,row.barcode,row.sku,row.price,row.version)==before
        assert db.query(RetailIdentifier).count()==1
    response=r.post('labels/preview',{'selection':token,'profile':labels.SHOPIFY_COMPACT,'barcode_source':'shopify'});assert response.status_code==200,response.text
    token1=response.headers['X-Retail-Preview'];response2=r.post('labels/preview',{'selection':token,'profile':labels.SHOPIFY_COMPACT,'barcode_source':'shopify'});assert response2.headers['X-Retail-Preview']!=token1
    response=r.post('labels/jobs',{'selection':token,'profile':labels.SHOPIFY_COMPACT,'barcode_source':'shopify','preview_token':token1,'default_copies':2,'confirm':True});assert response.status_code==409 # exact copies must match preview
    response=r.post('labels/jobs',{'selection':token,'profile':labels.SHOPIFY_COMPACT,'barcode_source':'shopify','preview_token':token1,'confirm':True});assert response.status_code==200,response.text
    with r.sessions() as db:
        job=db.query(RetailPrintJob).one();assert job.rows[0]['barcode']=='AB12';labels.ensure_reprint_safe(db,job)
        db.get(RetailVariant,1).price=150;db.commit()
        with pytest.raises(HTTPException):labels.ensure_reprint_safe(db,job)
    stale=r.post('labels/jobs',{'selection':token,'profile':labels.SHOPIFY_COMPACT,'barcode_source':'shopify','preview_token':token1,'confirm':True});assert stale.status_code==409


def test_scan_duplicates_missing_and_unsupported_fail(retail):
    r=retail;import_nodes(r,[node(1,sku='ONE',barcode='000012'),node(2,sku='TWO',barcode='000012')])
    assert r.client.get('/crm/retail/api/labels/scan?code=000012').status_code==409
    assert r.client.get('/crm/retail/api/labels/scan?code=UNKNOWN').status_code==404
    assert r.client.get('/crm/retail/api/labels/scan',params={'code':'සිංහල'}).status_code==422
    import_nodes(r,[node(1,sku='ONE',barcode='001234'),node(2,sku='TWO',barcode='OTHER')])
    result=r.client.get('/crm/retail/api/labels/scan?code=001234');assert result.status_code==200 and result.json()['row']['barcode']=='001234'


def test_long_barcode_rejected_even_on_60mm():
    for profile in [c(),labels.PRESETS['60x40']]:
        with pytest.raises(HTTPException) as e:labels.backend.render([item('LONG-BARCODE-123456')],profile)
        assert 'Barcode too long for this label profile' in e.value.detail


def test_browser_profile_refresh_race_and_original_scan(retail):
    import json
    from pathlib import Path
    from urllib.parse import urlsplit
    from playwright.sync_api import sync_playwright
    r=retail;import_nodes(r,[node(1,sku='4001946',barcode='AB12',price='140.00')]);token=choose(r,[1]);root=Path(__file__).parents[1]
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':1366,'height':900});errors=[];pending=[];held=[False]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script('window.confirm=()=>true;sessionStorage.setItem("retail-selection",'+json.dumps(json.dumps({'token':token,'count':1}))+')')
        def fulfill(h):
            req=h.request;u=urlsplit(req.url)
            res=r.client.request(req.method,u.path+('?'+u.query if u.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token'}})
            h.fulfill(status=res.status_code,body=res.content,headers={k:v for k,v in res.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
        def route(h):
            req=h.request;u=urlsplit(req.url)
            if u.hostname!='labels.test':h.fulfill(status=200,body='');return
            if u.path=='/crm/retail/api/labels/preview' and json.loads(req.post_data)['profile']=='50x30' and not held[0]:held[0]=True;pending.append(h);return
            fulfill(h)
        page.route('**/*',route);page.goto('https://labels.test/crm/retail/labels');page.locator('#label-selection-rows tr').wait_for()
        page.locator('#preview-labels').click();page.wait_for_function('document.querySelector("#label-preview-info").textContent.includes("Loading")')
        page.locator('#label-profile').select_option('30x15');page.wait_for_function('document.querySelector("#label-preview-info").textContent.startsWith("Ready:")');assert '30 × 15' in page.locator('#label-preview-info').inner_text()
        newest=page.locator('#label-preview').get_attribute('src');assert pending;fulfill(pending.pop());page.wait_for_timeout(150);assert page.locator('#label-preview').get_attribute('src')==newest
        page.locator('#label-original-code').fill('AB12');page.locator('#label-original-code').press('Enter');page.locator('#label-original-match').get_by_text('Original barcode: AB12',exact=False).wait_for()
        assert page.locator('#label-barcode-source').input_value()=='shopify'
        page.locator('#label-profile').select_option(labels.SHOPIFY_COMPACT);page.wait_for_function('document.querySelector("#label-preview-info").textContent.startsWith("Ready:")&&document.querySelector("#label-preview-info").textContent.includes("Shopify original")')
        page.locator('#label-copies').fill('2');page.wait_for_function('document.querySelector("#label-preview-info").textContent.startsWith("Ready:")');page.locator('#generate-labels').click();page.locator('#print-rows').get_by_text('2 · QUEUED',exact=False).wait_for()
        with r.sessions() as db:job=db.query(RetailPrintJob).one();assert job.rows[0]['barcode']=='AB12' and job.rows[0]['barcode_source']=='shopify' and job.template['content_layout']==labels.SHOPIFY_LAYOUT
        assert not errors;b.close()

@pytest.mark.parametrize('change',[{'price':None},{'sku':''},{'currency':'USD'}])
def test_original_scan_missing_fields_cannot_substitute(retail,change):
    r=retail;import_nodes(r,[node(1,sku='4001946',barcode='AB12',price='140.00')])
    with r.sessions() as db:
        v=db.get(RetailVariant,1)
        for k,value in change.items():setattr(v,k,value)
        db.commit()
    assert r.client.get('/crm/retail/api/labels/scan?code=AB12').status_code==422


def test_original_batch_is_exact_and_permission_enforced(retail):
    r=retail;import_nodes(r,[node(1,sku='ONE',barcode='AB12',price='350.00'),node(2,sku='TWO',barcode='000012',price='1860.00')]);token=choose(r,[1,2])
    with r.sessions() as db:
        rows,total=labels.print_rows(db,db.get(User,r.uid),token,default_copies=2,barcode_source='shopify');assert total==4
        raw=labels.backend.render(labels.labels(rows),c())
        with closing(pdfium.PdfDocument(raw)) as doc:assert [zxingcpp.read_barcodes(page.render(scale=203/72).to_pil())[0].text for page in doc]==['AB12','AB12','000012','000012']
        u=db.get(User,r.uid);u.role='STAFF';u.section_access=['retail.products.view'];db.commit()
    assert r.client.get('/crm/retail/api/labels/scan?code=AB12').status_code==403


def test_legacy_profiles_unchanged_and_standard_title_only_alias():
    for name in ['25x15','30x15','40x20','50x30','60x40']:
        expected=labels.preset(*map(int,name.split('x')))
        if name=='40x20':expected['barcode_height_mm']=7
        assert labels.PRESETS[name]==expected
    assert labels.PRESETS[labels.COMPACT_SKU_PRICE]['show_brand'] is False


def test_13_digit_original_larger_preset_contains_all_required_fields():
    raw=labels.backend.render([item('4792210130832')],labels.PRESETS[labels.SHOPIFY_LARGE]);page=PdfReader(io.BytesIO(raw)).pages[0]
    for value in ['PIXIEPINKS','4792210130832','SKU 4001946','Rs. 140.00']:assert value in page.extract_text()
    with closing(pdfium.PdfDocument(raw)) as doc:assert zxingcpp.read_barcodes(doc[0].render(scale=203/72).to_pil())[0].text=='4792210130832'


def test_compact_calibration_never_removes_safe_physical_margin():
    config=c();config['offset_x_mm']=.5
    with pytest.raises(HTTPException):labels.validate_config(config)
