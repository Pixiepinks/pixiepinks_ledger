from types import SimpleNamespace
import pytest
from test_sales_pipeline import fixture,inbox
from test_loyalty_browser import browser
from scripts.seed_sales_pipeline_scale import seed

@pytest.mark.parametrize('width,height',[(1648,928),(1920,1080),(1366,768),(412,915)])
def test_independent_scroll_stable_poll_and_column_loading(fixture,width,height):
    from playwright.sync_api import expect
    client,sessions,*_=fixture;seed(sessions,1000)
    with browser(SimpleNamespace(client=client),width) as (page,errors,calls):
        page.set_viewport_size({'width':width,'height':height})
        page.add_init_script("const nativeInterval=setInterval;window.setInterval=(fn,ms,...args)=>{if(ms===15000&&String(fn).includes('filters.open')){window.pipelinePoll=fn;return -1}return nativeInterval(fn,ms,...args)}")
        page.goto('https://loyalty.test/crm/sales-pipeline')
        interest=page.locator('[data-stage=INTERESTED] .pl-cards');expect(interest.locator('.pl-card')).to_have_count(50)
        assert page.locator('script[src*="tailwindcss"]').count()==0
        interest.locator('.pl-card').first.click();expect(page.locator('.pl-identity')).to_be_visible()
        if width>750:
            assert page.evaluate('document.documentElement.scrollHeight<=innerHeight+1')
            for header in page.locator('.pl-column>header').all():
                bounds=header.bounding_box();assert bounds['x']>=0 and bounds['x']+bounds['width']<=width
            page.evaluate("window.savedCards=document.querySelector('[data-stage=INTERESTED] .pl-cards');window.savedProfile=document.querySelector('.pl-profile');window.savedKPI=document.querySelector('.pl-kpis button');window.savedCards.scrollTop=320")
            before=page.locator('#pl-details>header').bounding_box()
            assert interest.evaluate('e=>e.scrollTop')==320
            assert page.locator('[data-stage=PAYMENT] .pl-cards').evaluate('e=>e.scrollTop')==0
            assert page.locator('[data-stage=FUTURE] .pl-cards').evaluate('e=>e.scrollTop')==0
            page.locator('.pl-profile').evaluate('e=>e.scrollTop=100')
            box=page.get_by_role('button',name='Set Payment Date').bounding_box();assert box['y']+box['height']<=height
            assert page.locator('#pl-details>header').bounding_box()==before
            with page.expect_response(lambda r:r.url.split('?')[0].endswith('/crm/sales-pipeline/api')):page.evaluate('window.pipelinePoll()')
            page.wait_for_timeout(100)
            assert page.evaluate("savedCards===document.querySelector('[data-stage=INTERESTED] .pl-cards')&&savedProfile===document.querySelector('.pl-profile')&&savedKPI===document.querySelector('.pl-kpis button')")
            assert interest.evaluate('e=>e.scrollTop')==320 and page.evaluate('scrollY')==0
            more=page.locator('[data-stage=INTERESTED] .pl-column-more');more.click();expect(interest.locator('.pl-card')).to_have_count(100);more.click();expect(interest.locator('.pl-card')).to_have_count(150)
            assert len(set(interest.locator('.pl-card').evaluate_all('(els)=>els.map(e=>e.dataset.id)')))==150
        else:
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            expect(page.locator('#pl-details')).to_have_class('mobile-open')
        assert not errors,errors


def test_inflight_poll_cannot_overwrite_open_edit_version(fixture):
    from playwright.sync_api import expect
    from sales_pipeline_models import SalesOpportunity as O
    client,sessions,ids,csrf,oid=fixture
    with browser(SimpleNamespace(client=client),1648) as (page,errors,calls):
        page.add_init_script("const native=setInterval;window.setInterval=(fn,ms,...a)=>{if(ms===15000&&String(fn).includes('filters.open')){window.pipelinePoll=fn;return -1}return native(fn,ms,...a)}")
        page.goto('https://loyalty.test/crm/sales-pipeline');page.locator('.pl-card').first.click();expect(page.locator('.pl-identity')).to_be_visible()
        held=[];url=f'https://loyalty.test/crm/sales-pipeline/api/{oid}'
        page.route(url,lambda route:held.append(route))
        with page.expect_request(url):page.evaluate('window.pipelinePoll()')
        page.get_by_role('button',name='Update Status').click()
        form=page.locator('#pl-edit form');form.locator('[name=note]').fill('Unsaved local note')
        changed=client.post(f'/crm/sales-pipeline/api/{oid}',json={'stage':'INTERESTED','version':0,'note':'Concurrent authoritative note'},headers={'X-CSRF-Token':csrf,'Idempotency-Key':'concurrent-editor-key'})
        assert changed.status_code==200,changed.text
        response=client.get(f'/crm/sales-pipeline/api/{oid}');held[0].fulfill(status=200,body=response.content,headers={'content-type':'application/json'});page.unroute(url)
        page.wait_for_timeout(100);form.get_by_role('button',name='Save commitment').click();expect(form.locator('.pl-error')).to_contain_text('changed')
        with sessions() as db:assert db.get(O,oid).data['note']=='Concurrent authoritative note'
        assert not errors
