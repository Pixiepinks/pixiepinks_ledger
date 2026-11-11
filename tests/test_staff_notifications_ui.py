"""Local Chromium UI and service-worker event simulation, with no live push service."""
import shutil
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from test_whatsapp_quick_messages import inbox


@pytest.mark.parametrize('width', [1280, 390, 320])
def test_settings_bell_permissions_and_mobile_layout(inbox, width):
    api = pytest.importorskip('playwright.sync_api'); chromium = shutil.which('chromium')
    if not chromium: pytest.skip('Chromium unavailable')
    client = inbox[0]
    with api.sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width': width, 'height': 900}); errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def serve(route):
                request = route.request; url = urlsplit(request.url)
                if url.hostname != 'staff-push.test':
                    route.fulfill(status=200, body=''); return
                response = client.request(request.method, url.path + ('?' + url.query if url.query else ''),
                    content=request.post_data_buffer, headers={k: v for k, v in request.headers.items() if k in {'content-type', 'x-csrf-token'}})
                route.fulfill(status=response.status_code, body=response.content,
                    headers={k: v for k, v in response.headers.items() if k in {'content-type', 'cache-control'}})
            page.route('**/*', serve)
            page.goto('https://staff-push.test/crm/notifications'); page.wait_for_load_state('networkidle')
            api.expect(page.locator('#notification-diagnostics')).to_contain_text('Server Web Push is not configured')
            assert page.locator('input[name=reminders_enabled]').is_checked() is False
            assert page.locator('input[name=escalations_enabled]').is_checked() is False
            page.locator('#staff-alert-bell').click(); api.expect(page.locator('#staff-alert-panel')).to_be_visible()
            page.locator('#staff-alert-bell').click()
            page.locator('input[name=messages]').uncheck()
            page.locator('#notification-preferences button[type=submit]').click()
            page.wait_for_load_state('networkidle')
            assert not client.get('/crm/notifications/api').json()['preferences']['messages']
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Horizontal overflow'
            assert not errors, errors
            page.screenshot(path=f'/tmp/staff-notifications-{width}.png', full_page=True)
        finally:
            browser.close()


def test_worker_deduplicates_and_reuses_window_and_rechecks_click_permissions():
    api = pytest.importorskip('playwright.sync_api'); chromium = shutil.which('chromium')
    if not chromium: pytest.skip('Chromium unavailable')
    source = (Path(__file__).resolve().parents[1] / 'static/staff_push_sw.js').read_text()
    with api.sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        try:
            page = browser.new_page()
            page.route('https://staff-worker.test/**', lambda route: route.fulfill(status=200, body='<html></html>', content_type='text/html'))
            page.goto('https://staff-worker.test/')
            result = page.evaluate('''async source => {
              const handlers={}, shown=[], navigated=[], opened=[], messages=[], reads=[];
              let permitted=true;
              const client={url:'https://staff-worker.test/crm/whatsapp',focused:true,
                postMessage:m=>messages.push(m),navigate:async url=>navigated.push(url),focus:async()=>{}};
              const worker={location:{origin:'https://staff-worker.test'}, addEventListener:(kind,fn)=>handlers[kind]=fn,
                registration:{getNotifications:async()=>[], showNotification:async(title,options)=>shown.push(options)},
                clients:{matchAll:async()=>[client],openWindow:async url=>opened.push(url)}};
              const fetcher=async(path,options)=>{if(options.method==='POST')reads.push({path,csrf:options.headers['X-CSRF-Token']});return {ok:permitted,json:async()=>({id:7,title:'Alert',body:'Customer message',sound:true,csrf_token:'csrf-test',url:'/crm/whatsapp?conversation=42'})};};
              new Function('self','fetch',source)(worker,fetcher);
              const run=async(kind,extra)=>{let promise;handlers[kind]({...extra,waitUntil:p=>promise=p});await promise;};
              await run('push',{data:{json:()=>({id:7})}});
              await run('push',{data:{json:()=>({id:7})}});
              await run('notificationclick',{notification:{close:()=>{},data:{id:7}}});
              permitted=false;
              await run('push',{data:{json:()=>({id:8})}});
              await run('notificationclick',{notification:{close:()=>{},data:{id:7}}});
              return {shown,navigated,opened,messages,reads};
            }''', source)
            assert len(result['shown']) == 1 and result['shown'][0]['silent'] is True
            assert result['navigated'] == ['https://staff-worker.test/crm/whatsapp?conversation=42', 'https://staff-worker.test/crm/notifications']
            assert not result['opened'] and len(result['messages']) == 1
            assert result['reads'] == [{'path': '/crm/notifications/api/7/read', 'csrf': 'csrf-test'}]
        finally:
            browser.close()


def test_background_subscription_renewal_requires_current_opted_in_device():
    api = pytest.importorskip('playwright.sync_api'); chromium = shutil.which('chromium')
    if not chromium: pytest.skip('Chromium unavailable')
    source = (Path(__file__).resolve().parents[1] / 'static/staff_push_sw.js').read_text()
    with api.sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        try:
            page = browser.new_page()
            result = page.evaluate('''async source => {
              const handlers={}, posts=[]; let active=true;
              const worker={addEventListener:(kind,fn)=>handlers[kind]=fn,clients:{matchAll:async()=>[]}};
              const fetcher=async(path, options)=>{
                if(options.method==='POST'){posts.push({path,headers:options.headers});return {ok:true};}
                return {ok:true,json:async()=>({push_configured:true,csrf_token:'csrf-test',devices:[{current:true,active}]})};
              };
              new Function('self','fetch',source)(worker,fetcher);
              const run=async()=>{let promise;handlers.pushsubscriptionchange({newSubscription:{toJSON:()=>({endpoint:'test'})},waitUntil:p=>promise=p});await promise;};
              await run(); active=false; await run(); return posts;
            }''', source)
            assert len(result) == 1 and result[0]['path'] == '/crm/notifications/api/devices'
            assert result[0]['headers']['X-CSRF-Token'] == 'csrf-test'
        finally:
            browser.close()
