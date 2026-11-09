import shutil
from urllib.parse import urlsplit
import pytest
from models import User
from test_whatsapp_quick_messages import inbox
from test_meta_ads import seed, FakeClient
from meta_ads_worker import tick


def configure(monkeypatch):
    for k,v in {'META_ADS_ACCOUNT_ID':'123','META_ADS_ACCESS_TOKEN':'test-secret-do-not-print','META_ADS_SYNC_ENABLED':'true'}.items():monkeypatch.setenv(k,v)


def test_server_authorization_csrf_private_export_and_filters(inbox,monkeypatch):
    configure(monkeypatch)
    client,sessions,ids,sends,login,csrf=inbox
    tick(sessions,client_factory=FakeClient)
    with sessions() as db:seed(db)
    r=client.get('/crm/ad-analytics/api');assert r.status_code==200,r.text
    assert r.headers['cache-control']=='private, no-store'
    assert 'test-secret' not in r.text and r.json()['summary']['revenue']==1000
    assert client.get('/crm/ad-analytics/api?sort=invalid').status_code==422
    assert client.get('/crm/ad-analytics/api?level=campaign&ad=33').status_code==422
    export=client.get('/crm/ad-analytics/export');assert export.status_code==200 and 'LKR' in export.text and 'ROAS' not in export.text
    assert client.post('/crm/ad-analytics/sync').status_code==403
    invoked=[];monkeypatch.setattr('meta_ads_worker.tick',lambda *a:invoked.append(True))
    assert client.post('/crm/ad-analytics/sync',headers={'X-CSRF-Token':r.json()['csrf_token']}).status_code==200
    assert invoked and not sends
    for who in ['quick-staff','quick-agent','quick-denied','quick-manager']:
        login(who);assert client.get('/crm/ad-analytics/api').status_code==403
        assert client.get('/crm/ad-analytics').status_code==403
        assert client.get('/crm/ad-analytics/export').status_code==403
    with sessions() as db:
        db.query(User).filter_by(username='quick-manager').one().section_access=['ad_analytics'];db.commit()
    login('quick-manager');assert client.get('/crm/ad-analytics/api').status_code==200
    assert client.post('/crm/ad-analytics/sync').status_code==403
    assert client.get('/crm/ad-analytics/export').status_code==403


def test_additive_migration_can_create_tables_and_rerun(inbox):
    _,sessions,*_=inbox
    from meta_ads_models import MetaAdsDaily,MetaAdsSync
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    with sessions() as db:bind=db.get_bind()
    MetaAdsDaily.__table__.drop(bind);MetaAdsSync.__table__.drop(bind)
    cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(cfg,'20261108_34');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with sessions() as db:assert db.query(MetaAdsDaily).count()==0


def test_browser_desktop_mobile_and_detail(inbox,monkeypatch):
    configure(monkeypatch)
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,*_=inbox;tick(sessions,client_factory=FakeClient)
    with sessions() as db:seed(db)
    def serve(page):
        def handle(route):
            req=route.request;u=urlsplit(req.url)
            response=client.request(req.method,u.path+('?' +u.query if u.query else ''),headers={k:v for k,v in req.headers.items() if k not in {'host','cookie'}},content=req.post_data_buffer,follow_redirects=False)
            route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
        page.route('**/*',handle)
    errors=[]
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            for width in [1366,390,320]:
                page=browser.new_page(viewport={'width':width,'height':900});page.on('pageerror',lambda e:errors.append(str(e)));serve(page)
                page.goto('https://ads.test/crm/ad-analytics')
                api.expect(page.locator('#ads-feedback')).to_contain_text('Report loaded')
                api.expect(page.locator('#ads-kpis .ads-kpi')).to_have_count(9)
                api.expect(page.locator('#ads-rows tr')).to_have_count(2)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=f'/tmp/meta-ads-{width}.png',full_page=True)
                page.goto('https://ads.test/crm/ad-analytics/ads/33')
                api.expect(page.locator('#ads-details')).to_contain_text('SO-1')
                api.expect(page.locator('#ads-details')).to_contain_text('Daily spend')
                page.locator('[name=interest]').fill('Toys');page.get_by_role('button',name='Apply',exact=True).click()
                api.expect(page.locator('#ads-details')).to_contain_text('Inquiries (0;')
                assert 'test-secret' not in page.content()
                page.close()
            assert not errors
        finally:browser.close()
