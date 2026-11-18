"""Imported metadata filters must work before manual retail categories are mapped."""
import csv
import io
import pytest
from retail_models import RetailVariant,RetailPreview
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail,node,import_nodes,choose


def catalog(r):
    values=[]
    for i,title,kind,vendor,size,collection in [
        (1,'Kids Bicycle Blue','Bicycles','Acme','16 inches',1),
        (2,'Kids Bicycle Red','Bicycles','Acme','20 inches',1),
        (3,'Kids Projector Torch Green','Toys','Other','Default Title',2),
        (4,'Bicycle Repair Kit','Accessories','Other','Default Title',2)]:
        value=node(i,sku=str(1000+i),barcode='P'+str(i),title=size)
        value['product'].update(title=title,productType=kind,vendor=vendor,
            collections={'nodes':[{'id':f'gid://shopify/Collection/{collection}','title':'Bikes' if collection==1 else 'Gifts'}], 'pageInfo':{'hasNextPage':False}})
        values.append(value)
    import_nodes(r,values)


def test_imported_types_and_collections_find_unmapped_products(retail):
    r=retail;catalog(r)
    options=r.client.get('/crm/retail/api/filter-options').json()
    assert options['total']==4
    assert {'value':'Bicycles','label':'Bicycles','count':2} in options['product_types']
    assert {'value':'__unmapped__','label':'Unmapped','count':4} in options['categories']
    assert next(item for item in options['categories'] if item['value']=='BIC')['count']==0
    data=r.client.get('/crm/retail/api/products',params={'product_type':'Bicycles'}).json()
    assert data['total']==2 and all(row['category']=='' for row in data['rows'])
    assert r.client.get('/crm/retail/api/products?category=BIC').json()['total']==0
    assert r.client.get('/crm/retail/api/products?category=__unmapped__').json()['total']==4
    assert r.client.get('/crm/retail/api/products',params={'collection':'gid://shopify/Collection/1'}).json()['total']==2
    assert r.client.get('/crm/retail/api/products',params={'product_type':'Bicycles','vendor':'Other'}).json()['total']==0


@pytest.mark.parametrize('term,ids', [('  bicycle   16  ',[1]),('16 blue',[1]),('ACME RED',[2]),('P3',[3]),('1001',[1]),('bicycle',[1,2,4]),('%',[]),('_',[])])
def test_multiword_trimmed_literal_search_across_fields(retail,term,ids):
    r=retail;catalog(r)
    result=r.client.get('/crm/retail/api/products',params={'q':term}).json()
    assert [row['id'] for row in result['rows']]==ids


def test_exact_identifier_first_and_csv_bulk_selection_share_filters(retail):
    r=retail;catalog(r)
    with r.sessions() as db:
        db.get(RetailVariant,1).barcode='P30';db.commit()
    assert r.client.get('/crm/retail/api/products?q=P3').json()['rows'][0]['id']==3
    filters={'product_type':'Bicycles','vendor':'Acme','collection':'gid://shopify/Collection/1','category':'__unmapped__','q':'16'}
    result=r.client.get('/crm/retail/api/products',params=filters).json();assert result['total']==1
    token=choose(r,filters=filters)
    with r.sessions() as db:assert db.get(RetailPreview,token).payload['ids']==[1]
    exported=r.client.get('/crm/retail/api/export.csv',params=filters)
    rows=list(csv.reader(io.StringIO(exported.text)));assert len(rows)==2 and rows[1][0]=='1'
    with r.sessions() as db:assert all(row.category is None for row in db.query(RetailVariant))


@pytest.mark.parametrize('filters',[{'product_type':[]},{'vendor':[]},{'collection':[]},{'collection':'wrong'},{'product_type':'x'*201}])
def test_bad_metadata_filters_are_rejected(retail,filters):
    assert retail.post('selection',{'filters':filters}).status_code==422


def test_filter_metadata_is_shop_scoped_and_permission_protected(retail):
    r=retail;catalog(r)
    with r.sessions() as db:
        row=db.get(RetailVariant,1);row.shop='other-shop';db.commit()
    data=r.client.get('/crm/retail/api/filter-options').json()
    assert data['total']==3 and next(item for item in data['product_types'] if item['value']=='Bicycles')['count']==1
    r.login('quick-staff');assert r.client.get('/crm/retail/api/filter-options').status_code==403


def test_browser_filters_apply_automatically_reset_and_freeze_correct_selection(retail):
    import shutil
    from urllib.parse import urlsplit,parse_qs
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    r=retail;catalog(r)
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':1366,'height':950});errors=[];requests=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def serve(route):
            req=route.request;url=urlsplit(req.url)
            if url.hostname!='filters.test':route.fulfill(status=200,body='');return
            requests.append((url.path,parse_qs(url.query)))
            response=r.client.request(req.method,url.path+('?'+url.query if url.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k in {'content-type','x-csrf-token'}},follow_redirects=False)
            route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
        page.route('**/*',serve);page.goto('https://filters.test/crm/retail/products')
        page.locator('#page-info').get_by_text('1–4 of 4',exact=True).wait_for()
        assert not page.locator('#allocation').get_attribute('open')
        assert page.locator('select[name=category] option[value=BIC]').evaluate('el=>el.disabled'), page.locator('select[name=category]').evaluate('el=>el.outerHTML')
        page.get_by_label('Shopify product type',exact=True).select_option('Bicycles')
        page.locator('#page-info').get_by_text('1–2 of 2',exact=True).wait_for()
        search=page.locator('input[name=q]');search.fill('bicycle 16')
        page.locator('#page-info').get_by_text('1–1 of 1',exact=True).wait_for()
        assert 'Kids Bicycle Blue' in page.locator('#product-rows').inner_text()
        page.get_by_role('button',name='Select all filtered results',exact=True).click()
        page.locator('#selection-count').get_by_text('1 variants in a frozen selection.',exact=False).wait_for()
        with r.sessions() as db:assert db.query(RetailPreview).filter_by(kind='SELECTION').one().payload['ids']==[1]
        page.get_by_role('button',name='Clear filters',exact=True).click()
        page.locator('#page-info').get_by_text('1–4 of 4',exact=True).wait_for()
        assert '1 variants in a frozen selection.' in page.locator('#selection-count').inner_text()
        page.get_by_role('button',name='Clear selection',exact=True).click()
        # Rapid edits while previous searches are active must settle on the final text.
        search.fill('bicycle');page.get_by_role('button',name='Search',exact=True).click();search.fill('torch')
        page.locator('#product-rows').get_by_text('Kids Projector Torch Green',exact=True).wait_for()
        assert 'Bicycle' not in page.locator('#product-rows').inner_text()
        assert not errors
        page.screenshot(path='/tmp/retail-product-filters.png',full_page=True)
        browser.close()
