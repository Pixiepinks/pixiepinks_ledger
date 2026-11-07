import shutil
from datetime import datetime,timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit
import pytest
from models import User,WhatsAppConversation as Conversation,WhatsAppConversationMessage as Message,Lead,WhatsAppMessageReferral as Referral
from test_whatsapp_quick_messages import inbox


def seed(v):
    client,sessions,ids,*_=v;now=datetime.utcnow()-timedelta(seconds=2)
    with sessions() as db:
        password=db.query(User).first().password_hash
        a=User(username='amanda',role='STAFF',password_hash=password,section_access=['crm','whatsapp'])
        k=User(username='kasun',role='STAFF',password_hash=password,section_access=['crm','whatsapp']);db.add_all([a,k]);db.flush()
        names=['Amaya Perera','Nimal Silva','Shalini Fernando'];customers=[]
        for i,name in enumerate(names):
            c=Conversation(phone_number='9477001250'+str(i),customer_name=name,assigned_to_user_id=a.id if i==0 else k.id if i==1 else None,last_customer_message_at=now)
            db.add(c);db.flush();customers.append(c)
            for j,kind in enumerate(['staff','staff','staff_saved_voice','staff_audio','staff_product']):
                db.add(Message(phone_number=c.phone_number,direction='outbound',message_text='Fixture message',response_kind=kind,
                    sent_by='amanda' if (i+j)%2==0 else 'kasun',send_status='read' if j%2 else 'delivered',whatsapp_message_id=f'wamid.dashboard-{i}-{j}',created_at=now-timedelta(minutes=15)))
            incoming=Message(phone_number=c.phone_number,direction='inbound',message_text='Fixture customer inquiry',whatsapp_message_id='wamid.customer-'+str(i),created_at=now-timedelta(minutes=8+i))
            db.add(incoming);db.flush()
            if i==0:db.add(Referral(message_id=incoming.id,source_type='ad',source_id='fixture-ad',captured_at=now))
        statuses=['NEW','NEW','NEW','NEGOTIATING','NEGOTIATING','PAYMENT_PENDING','ORDER_CONFIRMED','ORDER_CONFIRMED']
        for i,status in enumerate(statuses):
            c=customers[i] if i<3 else Conversation(phone_number='9477001260'+str(i),customer_name='Pipeline contact '+str(i),assigned_to_user_id=a.id)
            if i>=3:db.add(c);db.flush()
            db.add(Lead(customer_name=c.customer_name,mobile=c.phone_number,source='WhatsApp',status=status,whatsapp_conversation_id=c.id,created_at=now))
        db.commit()


def serve(page,client,fail):
    def handle(route):
        request=route.request;url=urlsplit(request.url)
        if url.hostname!='dashboard.test':route.fulfill(status=200,body='');return
        if fail.get('enabled') and url.path=='/crm/dashboard/api':route.fulfill(status=503,json={'detail':'Dashboard unavailable fixture.'});return
        response=client.request(request.method,url.path+('?' +url.query if url.query else ''),follow_redirects=False)
        route.fulfill(status=response.status_code,body=response.content,headers={key:value for key,value in response.headers.items() if key not in {'content-length','content-encoding','set-cookie'}})
    page.route('**/*',handle)


def test_dashboard_desktop_mobile_filters_drilldowns_and_error_retention(inbox):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    seed(inbox);client,sessions,ids,*_=inbox;failure={'enabled':False}
    expected=client.get('/crm/dashboard/api').json();assert expected['metrics']['staff_sent']==15
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)));serve(page,client,failure)
            page.goto('https://dashboard.test/crm?period=today')
            metric=page.locator('[data-focus-key="metric-staff_sent"]')
            api.expect(metric.locator('strong')).to_have_text('15')
            api.expect(page.locator('#dash-staff tr')).to_have_count(2)
            api.expect(page.locator('#dash-pipeline .dash-stage')).to_have_count(4)
            assert page.locator('#dash-breakdown button').count()==9
            page.screenshot(path='/tmp/crm-dashboard-desktop.png',full_page=True)
            metric.click();api.expect(page.locator('#dash-details')).to_be_visible();api.expect(page.locator('#dash-details-status')).to_contain_text('15 matching records')
            page.locator('#dash-details-close').click();api.expect(metric).to_be_focused()
            page.locator('[name="source"]').select_option('ad');page.get_by_role('button',name='Apply filters',exact=True).click()
            api.expect(page.locator('[data-focus-key="metric-incoming"] strong')).to_have_text('1')
            assert 'source=ad' in page.url
            page.locator('[data-period="last_month"]').click();api.expect(metric.locator('strong')).to_have_text('0')
            api.expect(page.locator('[data-focus-key="queue-needs_reply"]')).to_contain_text('1')
            page.locator('#dash-refresh').click();api.expect(page.locator('[name="source"]')).to_have_value('ad')
            failure['enabled']=True;page.locator('#dash-refresh').click()
            api.expect(page.locator('#dash-feedback')).to_contain_text('Dashboard unavailable fixture.')
            api.expect(metric.locator('strong')).to_have_text('0')
            failure['enabled']=False
            for width in [320,360,390,430]:
                mobile=browser.new_page(viewport={'width':width,'height':812});mobile.on('pageerror',lambda e:errors.append(str(e)));serve(mobile,client,failure)
                mobile.goto('https://dashboard.test/crm?period=today')
                api.expect(mobile.locator('[data-focus-key="metric-staff_sent"] strong')).to_have_text('15')
                assert mobile.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not mobile.locator('#dash-advanced').evaluate('(el)=>el.open')
                if width==390:mobile.screenshot(path='/tmp/crm-dashboard-mobile.png',full_page=True)
                mobile.close()
            assert not errors
        finally:browser.close()
