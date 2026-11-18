"""30×15 compact SKU/price output: exact fields and independent 203-dpi decoding."""
import copy
import io
from contextlib import closing
from decimal import Decimal
import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from pypdf.generic import ContentStream
import pypdfium2 as pdfium
import zxingcpp
import retail_labels as labels
from retail_models import RetailVariant,RetailIdentifier
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail,node,import_nodes,choose

PROFILE=labels.COMPACT_SKU_PRICE


def config():return copy.deepcopy(labels.PRESETS[PROFILE])

def example(**values):
    return labels.Label(**{'barcode':'P2','title':'Magical Kids Water Book','variant':'Hidden variant',
        'sku':'1001150','price':'1500.00','currency':'LKR',**values})


def render(item=None,settings=None):return labels.backend.render([item or example()],settings or config())


def test_pdf_dimensions_content_font_sizes_order_and_bar_geometry():
    reader=PdfReader(io.BytesIO(render()));page=reader.pages[0]
    assert float(page.mediabox.width)*25.4/72==pytest.approx(30,abs=.01)
    assert float(page.mediabox.height)*25.4/72==pytest.approx(15,abs=.01)
    assert page.extract_text().splitlines()==['Magical Kids Water Book','P2 | SKU: 1001150','Rs. 1,500.00']
    pieces=[]
    page.extract_text(visitor_text=lambda text,cm,tm,font,size:pieces.append((text.strip(),size,tm[5]*25.4/72)) if text.strip() else None)
    by_text={value:(size,y) for value,size,y in pieces}
    assert by_text['Magical Kids Water Book'][0]==5
    assert by_text['P2 | SKU: 1001150'][0]==4.5
    assert by_text['Rs. 1,500.00'][0]==5
    bars=[]
    for operands,op in ContentStream(page.get_contents(),reader).operations:
        if op==b're':bars.append([float(v)*25.4/72 for v in operands])
    assert bars and all(abs(h)==pytest.approx(5,abs=.01) for x,y,w,h in bars)
    assert all(w/labels.DOT>=1.98 and w/labels.DOT==pytest.approx(round(w/labels.DOT),abs=.04) for x,y,w,h in bars)
    assert min(x for x,y,w,h in bars)>=.5+20*labels.DOT-.07
    assert 30-max(x+w for x,y,w,h in bars)>=.5+20*labels.DOT-.07
    bar_top=max(y for x,y,w,h in bars);bar_bottom=min(y+h for x,y,w,h in bars)
    assert by_text['Magical Kids Water Book'][1]>bar_top>bar_bottom>by_text['P2 | SKU: 1001150'][1]>by_text['Rs. 1,500.00'][1]>0


@pytest.mark.parametrize('margin',[.5,.625,.75])
@pytest.mark.parametrize('value',['P2','P10','001234'])
def test_decodes_exact_code128_at_203_dpi(margin,value):
    c=config();c['margin_mm']=margin
    with closing(pdfium.PdfDocument(render(example(barcode=value),c))) as document:
        image=document[0].render(scale=203/72).to_pil();codes=zxingcpp.read_barcodes(image)
        assert len(codes)==1 and codes[0].format==zxingcpp.BarcodeFormat.Code128 and codes[0].text==value


def test_long_product_name_is_safely_shortened_but_identifiers_and_price_are_exact():
    raw=render(example(title='Magical Kids Water Book with assorted beautiful pictures '*8))
    lines=PdfReader(io.BytesIO(raw)).pages[0].extract_text().splitlines()
    assert len(lines)==3 and lines[0].endswith('…')
    assert lines[1:] == ['P2 | SKU: 1001150','Rs. 1,500.00']
    assert 'Hidden variant' not in lines[0] and 'PIXIEPINKS' not in '\n'.join(lines)
    # Sinhala shaping and conjunct-safe shortening remain supported.
    assert render(example(title='ශ්‍රී ලංකා ළමා බයිසිකලය '*20))


@pytest.mark.parametrize('change,error',[
    ({'sku':''},'requires an SKU'),({'price':''},'selling price'),({'currency':'USD'},'in LKR'),
    ({'sku':'SKU-'+('1'*80)},'cannot fit together'),({'sku':'ABC\n123'},'one line'),
    ({'barcode':'LONG-ASSIGNED-BARCODE-12345'},'cannot fit'),
    ({'price':'999999999999999999999999.00'},'Selling price cannot fit'),
    ({'price':'NaN'},'selling price'),({'price':'-1.00'},'selling price'),({'price':'1.001'},'selling price')])
def test_required_fields_fail_clearly_instead_of_clipping(change,error):
    with pytest.raises(HTTPException) as failure:render(example(**change))
    assert failure.value.status_code==422 and error in failure.value.detail


@pytest.mark.parametrize('change',[{'margin_mm':.4},{'margin_mm':.8},{'font_pt':4.5},
    {'show_brand':True},{'show_variant':True},{'show_sku':False},{'show_price':False},
    {'barcode_height_mm':6},{'module_dots':1},{'width_mm':25}])
def test_compact_profile_contract_is_validated(change):
    c=config();c.update(change)
    with pytest.raises(HTTPException):labels.validate_config(c)


def test_existing_profiles_and_content_are_unchanged():
    assert labels.PRESETS['30x15']=={
        'width_mm':30,'height_mm':15,'margin_mm':1,'module_dots':2,'barcode_height_mm':6,
        'font_pt':5,'max_name_chars':60,'show_brand':False,'show_variant':False,'show_sku':False,
        'show_price':False,'labels_per_row':1,'layout':'roll','gap_mm':2,'offset_x_mm':0,'offset_y_mm':0,'density':8,'calibration':'gap'}
    assert PdfReader(io.BytesIO(render(example(title='Product',variant='Blue'),labels.PRESETS['30x15']))).pages[0].extract_text().splitlines()==['Product · Blue','P2']
    assert PdfReader(io.BytesIO(render(example(title='Product',variant='Blue'),labels.PRESETS['50x30']))).pages[0].extract_text().splitlines()==['PIXIEPINKS','Product','Blue','P2','SKU 1001150']


def test_profile_api_uses_correct_variant_selling_price_and_preserves_assignments(retail):
    r=retail;a=node(1,sku='1001150',barcode='P2',price='1500.00',compareAtPrice='2600.00')
    a['product']['title']='Magical Kids Water Book'
    b=node(2,sku='1001151',barcode='P3',price='3500.00');b['product']=copy.deepcopy(a['product'])
    import_nodes(r,[a,b]);selection=choose(r,[1])
    profiles=r.client.get('/crm/retail/api/profiles').json()['rows']
    assert next(p for p in profiles if p['id']==PROFILE)['title']=='30x15 Compact — SKU & Price'
    response=r.post('labels/preview',{'selection':selection,'profile':PROFILE});assert response.status_code==200,response.text
    assert PdfReader(io.BytesIO(response.content)).pages[0].extract_text().splitlines()==['Magical Kids Water Book','P2 | SKU: 1001150','Rs. 1,500.00']
    job=r.post('labels/jobs',{'selection':selection,'profile':PROFILE,'confirm':True})
    assert job.status_code==200,job.text
    assert labels.dispatch_one(r.sessions)
    assert r.client.get(f"/crm/retail/api/labels/jobs/{job.json()['id']}/pdf").status_code==200
    with r.sessions() as db:
        row=db.get(RetailVariant,1)
        assert row.barcode=='P2' and row.sku=='1001150' and row.price==Decimal('1500.00')
        assert db.query(RetailIdentifier).count()==0


@pytest.mark.parametrize('currency',['USD',None,''])
def test_missing_or_non_lkr_currency_is_not_silently_assumed(retail,currency):
    r=retail;import_nodes(r,[node(1,sku='1001150',barcode='P2',price='1500.00')]);selection=choose(r,[1])
    with r.sessions() as db:db.get(RetailVariant,1).currency=currency;db.commit()
    response=r.post('labels/preview',{'selection':selection,'profile':PROFILE})
    assert response.status_code==422 and 'LKR' in response.json()['detail']


def test_all_selected_required_fields_checked_even_after_five_label_preview(retail):
    r=retail;values=[node(i,sku='1001150',barcode='P'+str(i),price='1500.00') for i in range(1,7)]
    values[-1]['sku']='1'*90;import_nodes(r,values)
    response=r.post('labels/preview',{'selection':choose(r,filters={}), 'profile':PROFILE})
    assert response.status_code==422 and 'cannot fit together' in response.json()['detail']


def test_profile_is_selectable_and_saved_custom_margin_retains_layout(retail):
    import json
    import shutil
    from urllib.parse import urlsplit
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    r=retail;import_nodes(r,[node(1,sku='1001150',barcode='P2',price='1500.00')]);selection=choose(r,[1])
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1280,'height':1000});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script('window.confirm=()=>true;sessionStorage.setItem("retail-selection",'+json.dumps(json.dumps({'token':selection,'count':1}))+')')
        def serve(route):
            req=route.request;url=urlsplit(req.url)
            if url.hostname!='compact.test':route.fulfill(status=200,body='');return
            response=r.client.request(req.method,url.path+('?'+url.query if url.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token'}},follow_redirects=False)
            route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
        page.route('**/*',serve);page.goto('https://compact.test/crm/retail/labels')
        page.locator('#label-profile').select_option(PROFILE)
        assert page.locator('#label-profile option:checked').inner_text()=='30x15 Compact — SKU & Price'
        page.locator('summary').click()
        assert page.get_by_label('Text size (pt)',exact=True).input_value()=='5'
        assert page.get_by_label('Text size (pt)',exact=True).is_disabled()
        assert page.get_by_label('Barcode height (mm)',exact=True).input_value()=='5'
        assert not page.get_by_label('Show PIXIEPINKS',exact=True).is_checked()
        assert not page.get_by_label('Separate variant line',exact=True).is_checked()
        assert page.get_by_label('Show SKU',exact=True).is_checked() and page.get_by_label('Show price',exact=True).is_checked()
        page.get_by_label('Margins (mm)',exact=True).fill('0.75');page.get_by_label('Margins (mm)',exact=True).dispatch_event('change')
        page.locator('#profile-title').fill('DBL compact calibrated margin')
        page.get_by_role('button',name='Save as new profile',exact=True).click()
        page.wait_for_function('document.querySelector("#label-profile").selectedOptions[0].textContent==="DBL compact calibrated margin"')
        page.get_by_role('button',name='Preview first 5 labels',exact=True).click()
        page.locator('#open-label-preview').wait_for()
        page.get_by_role('button',name='Generate PDF batch',exact=True).click()
        page.locator('#print-rows').get_by_text('1 · QUEUED',exact=False).wait_for()
        from retail_models import RetailPrintJob
        with r.sessions() as db:
            saved=db.query(RetailPrintJob).one()
            assert saved.template['content_layout']==labels.COMPACT_LAYOUT and saved.template['margin_mm']==.75
        assert not errors
        browser.close()
