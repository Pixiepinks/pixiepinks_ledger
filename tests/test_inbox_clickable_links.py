"""Readable, safely clickable website text and token-free interactive previews."""
import json
import shutil
from datetime import datetime
from urllib.parse import urlsplit
import pytest
from models import WhatsAppConversation as Conversation,WhatsAppConversationMessage as Message,MarketingConsent as Consent,WhatsAppMessageAttachment as Attachment
from test_marketing_consent_buttons import button_consent,sent,consent,inbox


def test_interactive_labels_are_scoped_token_free_and_read_only(button_consent):
    v=button_consent;preview,provider=sent(v)
    data=v.client.get(f'/crm/whatsapp/api/conversations/{v.ids[0]}').json()
    outgoing=next(m for m in data['messages'] if m['direction']=='outbound')
    assert outgoing['interactive_buttons']==['Yes, I agree','No, thanks']
    assert all(b['id'] not in json.dumps(data) for b in preview['buttons'])
    other=v.client.get(f'/crm/whatsapp/api/conversations/{v.ids[1]}').json()
    assert all(not m['interactive_buttons'] for m in other['messages'])
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='PENDING'
    assert len(v.button_sends)==1 and not v.sends


def test_browser_website_links_and_interactive_display(button_consent):
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=button_consent;sent(v)
    body='Visit www.pixiepinks.shop, or pixiepinks.shop/products?sort=new.\nHTTPS://www.pixiepinks.shop/a_(b) (https://example.com/path). http://example.com/\n<script>alert(1)</script> javascript:alert(1) https://user:secret@example.com user.name@example.com'
    with v.sessions() as db:
        row=Message(phone_number=db.get(Conversation,v.ids[0]).phone_number,direction='outbound',response_kind='staff',sent_by='quick-admin',message_text=body,whatsapp_message_id='wamid.link-fixture',created_at=datetime.utcnow())
        db.add(row);db.flush();mid=row.id
        media=Message(phone_number=row.phone_number,direction='outbound',response_kind='staff_media',message_text='Document',whatsapp_message_id='wamid.link-caption')
        db.add(media);db.flush();caption_id=media.id
        db.add(Attachment(message_id=media.id,media_id='fixture-document',media_type='document',filename='guide.pdf',caption='More: www.pixiepinks.shop'))
        db.commit()
    def serve(route):
        url=urlsplit(route.request.url)
        if url.hostname!='inbox.test':route.fulfill(status=200,body='');return
        response=v.client.request(route.request.method,url.path+('?' +url.query if url.query else ''),content=route.request.post_data_buffer,headers={k:value for k,value in route.request.headers.items() if k.lower() in {'content-type','x-csrf-token','idempotency-key'}})
        route.fulfill(status=response.status_code,body=response.content,headers={k:value for k,value in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
    with api.sync_playwright() as p:
        browser=p.chromium.launch(executable_path=chromium,args=['--no-sandbox']);page=browser.new_page(viewport={'width':390,'height':844});page.route('**/*',serve)
        page.goto(f'https://inbox.test/crm/whatsapp?conversation={v.ids[0]}')
        bubble=page.locator(f'[data-message-id="{mid}"] .bubble');bubble.wait_for()
        links=bubble.locator('a');assert links.count()==5
        assert links.nth(0).inner_text()=='www.pixiepinks.shop' and links.nth(0).get_attribute('href')=='https://www.pixiepinks.shop/'
        assert links.nth(1).get_attribute('href')=='https://pixiepinks.shop/products?sort=new'
        assert links.nth(2).get_attribute('href')=='https://www.pixiepinks.shop/a_(b)'
        assert links.nth(3).get_attribute('href')=='https://example.com/path'
        assert links.nth(4).get_attribute('href')=='http://example.com/'
        assert bubble.inner_text()==body and bubble.locator('script,img').count()==0
        for a in links.all():assert a.get_attribute('target')=='_blank' and 'noopener' in a.get_attribute('rel')
        assert page.locator(f'[data-message-id="{caption_id}"] .bubble p a').get_attribute('href')=='https://www.pixiepinks.shop/'
        options=page.locator('.message-button-options');assert options.locator('span').all_text_contents()==['Yes, I agree','No, thanks']
        assert options.locator('button,a').count()==0
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert len(v.button_sends)==1 and not v.sends
        assert page.get_by_text('Conversation not found or could not be loaded.',exact=True).count()==0
        page.screenshot(path='/tmp/inbox-clickable-links.png',full_page=True);browser.close()
