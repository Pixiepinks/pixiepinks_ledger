"""Authorized image routes and real browser fallbacks; no live creative requests."""
from urllib.parse import urlsplit
import shutil

from fastapi.testclient import TestClient
import pytest

import main
from models import WhatsAppMessageReferral
from referral_preview_service import PreviewError
from test_whatsapp_referral_cards import inbox, detail
from test_referral_preview_service import image_bytes

IMAGE = 'https://scontent.example.fbcdn.net/image.jpg?oh=private-reference'
THUMB = 'https://scontent.example.fbcdn.net/thumb.jpg'


def preview(client, cid, mid):
    message = next(row for row in detail(client,cid).json()['messages'] if row['id']==mid)
    return message['referral']


@pytest.mark.parametrize('metadata,kind,url',[
    ({'media_type':'image','image_url':IMAGE},'image',IMAGE),
    ({'media_type':'video','thumbnail_url':THUMB,'video_url':'https://video.example.fbcdn.net/ad.mp4'},'video_thumbnail',THUMB),
    ({'image_url':IMAGE},'image',IMAGE),
])
def test_scoped_preview_url_and_private_image_response(inbox,monkeypatch,metadata,kind,url):
    client,sessions,ids,add,_=inbox
    mid=add({**metadata,'headline':'Original advertisement','ctwa_clid':'private-click'})
    payload=preview(client,ids[0],mid)
    assert payload['preview_kind']==kind
    assert payload['preview_url']==preview(client,ids[0],mid)['preview_url']
    assert not any(value in str(payload) for value in [url,'fbcdn.net','private-click'])
    calls=[];png=image_bytes()
    monkeypatch.setattr(main,'get_preview',lambda *args:calls.append(args) or png)
    response=client.get(payload['preview_url'])
    assert response.status_code==200 and response.content==png
    assert response.headers['content-type']=='image/png'
    assert response.headers['cache-control']=='private, no-store'
    assert response.headers['x-content-type-options']=='nosniff'
    assert response.headers['content-disposition']=='inline; filename="advertisement-preview.png"'
    assert 'sandbox' in response.headers['content-security-policy']
    assert calls[0][0]==url and calls[0][1][1:3]==(ids[0],mid)


@pytest.mark.parametrize('metadata',[
    {'media_type':'video','video_url':'https://video.example.fbcdn.net/ad.mp4'},
    {'media_type':'image','image_url':'https://attacker.test/track'},
    {'image_url':'https://scontent.example.fbcdn.net/a?access_token=secret'},
    {'headline':'Text only'},
])
def test_missing_or_unsafe_preview_preserves_text_card(inbox,metadata):
    client,_,ids,add,_=inbox;mid=add({**metadata,'headline':'Retained text'})
    assert preview(client,ids[0],mid)=={'headline':'Retained text'}


def test_authorization_and_message_association_precede_retrieval(inbox,monkeypatch):
    client,sessions,ids,add,_=inbox
    mid=add({'image_url':IMAGE,'headline':'Ad'})
    url=preview(client,ids[0],mid)['preview_url']
    monkeypatch.setattr(main,'get_preview',lambda *a:pytest.fail('Unauthorized preview'))
    assert TestClient(main.app,base_url='https://testserver').get(url,follow_redirects=False).status_code==303
    with sessions() as db:rid=db.query(WhatsAppMessageReferral).one().id
    for cid,message,referral in [(ids[1],mid,rid),(ids[0],mid+999,rid),(ids[0],mid,rid+999),(999,mid,rid)]:
        assert client.get(f'/crm/whatsapp/api/conversations/{cid}/messages/{message}/referrals/{referral}/preview').status_code==404
    assert client.get(url.replace(url.split('v=')[1],'0'*24)).status_code==404


@pytest.mark.parametrize('status',[410,413,415,429,502,504])
def test_unavailable_preview_has_safe_error_and_retains_attribution(inbox,monkeypatch,status):
    client,sessions,ids,add,_=inbox;mid=add({'headline':'Retain this','image_url':IMAGE,'ctwa_clid':'private-click'})
    url=preview(client,ids[0],mid)['preview_url']
    def fail(*args):raise PreviewError(status)
    monkeypatch.setattr(main,'get_preview',fail)
    response=client.get(url)
    assert response.status_code==status and response.json()=={'detail':'Advertisement preview unavailable'}
    assert 'private-click' not in response.text and 'private-reference' not in response.text
    with sessions() as db:assert db.query(WhatsAppMessageReferral).one().headline=='Retain this'


def test_updated_stored_reference_changes_only_opaque_revision(inbox):
    client,sessions,ids,add,_=inbox;mid=add({'headline':'Ad','image_url':IMAGE})
    original=preview(client,ids[0],mid)['preview_url']
    with sessions() as db:
        db.query(WhatsAppMessageReferral).one().image_url=THUMB;db.commit()
    replacement=preview(client,ids[0],mid)['preview_url']
    assert replacement!=original and original.split('?')[0]==replacement.split('?')[0]
    assert client.get(original).status_code==404


def test_chromium_image_thumbnail_fallback_and_polling(inbox,monkeypatch):
    playwright=pytest.importorskip('playwright.sync_api')
    chromium=shutil.which('chromium') or shutil.which('chromium-browser')
    if not chromium:pytest.skip('Chromium unavailable')
    client,_,ids,add,_=inbox
    first=add({'headline':'Image advertisement','source_url':'https://fb.me/ad','media_type':'image','image_url':IMAGE,'ctwa_clid':'private-browser-click'})
    second=add({'headline':'Video advertisement','media_type':'video','thumbnail_url':THUMB,'video_url':'https://video.example.fbcdn.net/ad.mp4'})
    expired=add({'headline':'Expired but useful','source_url':'https://fb.me/expired','image_url':'https://scontent.example.fbcdn.net/expired'})
    absent=add({'headline':'No thumbnail','media_type':'video','video_url':'https://video.example.fbcdn.net/ad.mp4'})
    audio=add({'headline':'Voice ad'},media='voice')
    calls=[]
    def fetch(url,scope):
        calls.append(url)
        if url.endswith('/expired'):raise PreviewError(410)
        return image_bytes()
    monkeypatch.setattr(main,'get_preview',fetch)
    monkeypatch.setattr(main,'download_whatsapp_media',lambda *a:(b'OggS-test','audio/ogg'))
    with playwright.sync_playwright() as browser_api:
        browser=browser_api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1366,'height':900});requests=[];errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                parsed=urlsplit(route.request.url);requests.append(route.request.url)
                if parsed.hostname!='inbox.test':route.fulfill(status=200,body='');return
                response=client.request(route.request.method,parsed.path+('?' + parsed.query if parsed.query else ''),follow_redirects=False)
                headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-encoding','set-cookie'}}
                route.fulfill(status=response.status_code,headers=headers,body=response.content)
            page.route('**/*',serve);page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[0]}')
            for mid in (first,second):
                image=page.locator(f'[data-message-id="{mid}"] .ad-referral-preview img')
                image.scroll_into_view_if_needed();image.wait_for(state='visible')
                page.wait_for_function('(id)=>document.querySelector(`[data-message-id="${id}"] .ad-referral-preview img`).naturalWidth>0',arg=mid)
            assert page.locator(f'[data-message-id="{second}"] img').get_attribute('alt')=='Video advertisement thumbnail'
            expired_card=page.locator(f'[data-message-id="{expired}"] .ad-referral');expired_card.scroll_into_view_if_needed()
            page.wait_for_function('(id)=>document.querySelector(`[data-message-id="${id}"] .ad-referral-preview`).hidden',arg=expired)
            assert 'Expired but useful' in expired_card.inner_text() and expired_card.locator('a').count()==1
            assert page.locator(f'[data-message-id="{absent}"] .ad-referral-preview').count()==0
            assert page.locator('.ad-referral video').count()==0
            page.evaluate('window.kept=[...document.querySelectorAll(".ad-referral-preview img,.audio-bubble audio")]')
            before=list(calls);page.wait_for_timeout(4500)
            assert calls==before and page.evaluate('window.kept.every(node=>node.isConnected)')
            assert not any('fbcdn.net' in request or 'private-browser-click' in request or 'private-reference' in request for request in requests)
            assert 'private-browser-click' not in page.content()
            page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(250)
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            assert errors==[]
        finally:browser.close()
