"""Staff-only referral display, with isolated data and optional Chromium UI checks."""
import base64
from datetime import datetime, timedelta
import shutil
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import main
from models import (Base, User, WhatsAppConversation, WhatsAppConversationMessage,
                    WhatsAppMessageAttachment, WhatsAppMessageReferral)
from utils_auth import hash_password

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'cards.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    monkeypatch.setattr(main.settings, 'SALES_ORDERS_ENABLED', False)
    with sessions() as db:
        db.add(User(username='referral-reader', password_hash=hash_password('password'), role='STAFF'))
        conversation = WhatsAppConversation(phone_number='94770000000', mode='HUMAN', unread_count=4)
        other = WhatsAppConversation(phone_number='94771111111', mode='AI')
        db.add_all([conversation, other]); db.commit()
        ids = conversation.id, other.id
    client = TestClient(main.app, base_url='https://testserver')
    assert client.post('/login', data={'username':'referral-reader','password':'password'}).status_code == 200
    sequence = 0
    def add(referral=None, *, direction='inbound', phone='94770000000', media=None, attachment=None):
        nonlocal sequence
        sequence += 1
        with sessions() as db:
            message = WhatsAppConversationMessage(phone_number=phone, direction=direction,
                message_text=f'Customer message {sequence}', created_at=datetime(2026,1,1)+timedelta(seconds=sequence),
                media_id=media, media_mime_type='audio/ogg' if media else None)
            db.add(message); db.flush()
            if referral is not None:
                db.add(WhatsAppMessageReferral(message_id=message.id, **referral))
            if attachment is not None:
                db.add(WhatsAppMessageAttachment(message_id=message.id, **attachment))
            db.commit()
            return message.id
    yield client, sessions, ids, add, engine
    engine.dispose()


def detail(client, conversation):
    response = client.get(f'/crm/whatsapp/api/conversations/{conversation}')
    assert response.status_code == 200
    return response


def test_complete_referral_has_only_sanitized_display_fields(inbox):
    client, sessions, ids, add, engine = inbox
    mid = add({'source_id':'private-ad-id','ctwa_clid':'private-click-id','source_type':'ad',
               'source_url':'https://fb.me/example','headline':'Bicycle advertisement','body':'Advertisement description',
               'media_type':'video','image_url':'https://scontent.example.fbcdn.net/image.jpg',
               'video_url':'https://video.example.fbcdn.net/ad.mp4','thumbnail_url':'https://scontent.example.fbcdn.net/thumb.jpg'})
    statements = []
    def record(conn, cursor, statement, parameters, context, many):
        if 'whatsapp_message_referrals' in statement: statements.append(statement)
    event.listen(engine,'before_cursor_execute',record)
    try: response = detail(client,ids[0])
    finally: event.remove(engine,'before_cursor_execute',record)
    message = response.json()['messages'][0]
    assert message['id'] == mid
    assert message['referral'].pop('preview_kind') == 'video_thumbnail'
    assert message['referral'].pop('preview_url').startswith(
        f'/crm/whatsapp/api/conversations/{ids[0]}/messages/{mid}/referrals/')
    assert message['referral'] == {'source_type':'ad','source_url':'https://fb.me/example',
                                    'headline':'Bicycle advertisement','body':'Advertisement description'}
    assert response.headers['cache-control'] == 'private, no-store'
    for private in ['ctwa_clid','private-click-id','source_id','private-ad-id','image_url','video_url','thumbnail_url','fbcdn.net']:
        assert private not in response.text
    assert len(statements) == 1 and 'ctwa_clid' not in statements[0]
    with sessions() as db:
        conversation = db.get(WhatsAppConversation,ids[0])
        assert conversation.mode == 'HUMAN' and conversation.unread_count == 4
        assert db.query(WhatsAppMessageReferral).one().ctwa_clid == 'private-click-id'


@pytest.mark.parametrize('metadata,expected', [
    ({'headline':'Only headline'}, {'headline':'Only headline'}),
    ({'source_type':'POST'}, {'source_type':'post'}),
    ({'source_type':'instagram','headline':'Source unknown'}, {'headline':'Source unknown'}),
    ({'ctwa_clid':'private-only'}, None),
    (None, None),
])
def test_partial_and_missing_fields(inbox,metadata,expected):
    client,_,ids,add,_ = inbox
    add(metadata)
    assert detail(client,ids[0]).json()['messages'][0]['referral'] == expected


def test_description_is_short_and_storage_is_unchanged(inbox):
    client,sessions,ids,add,_ = inbox
    add({'headline':'h'*600,'body':'b'*4000})
    referral = detail(client,ids[0]).json()['messages'][0]['referral']
    assert len(referral['headline']) == 500
    assert len(referral['body']) == 300 and referral['body'].endswith('…')
    with sessions() as db: assert len(db.query(WhatsAppMessageReferral).one().body) == 4000


@pytest.mark.parametrize('url', [
    'javascript:alert(1)','data:text/html,evil','http://facebook.com/ad','//facebook.com/ad',
    'https://facebook.com.attacker.test/ad','https://user:password@facebook.com/ad','https://facebook.com:8443/ad',
    'https://facebook.com/ad?ctwa_clid=private','https://fb.me/ad?access_token=secret',
    'https://facebook.com/ad#token=secret','https://facebook.com/ad?access%255ftoken=secret',
])
def test_stored_unsafe_links_are_omitted_on_read(inbox,url):
    client,_,ids,add,_ = inbox
    add({'headline':'Safe text','source_url':url})
    assert detail(client,ids[0]).json()['messages'][0]['referral'] == {'headline':'Safe text'}


def test_multiple_ads_missing_referral_and_other_conversations_are_not_mixed(inbox):
    client,_,ids,add,_ = inbox
    first = add({'headline':'First advertisement'})
    second = add({'headline':'Second advertisement'})
    third = add()
    add({'headline':'Other customer'},phone='94771111111')
    outbound = add({'headline':'Must not display'},direction='outbound')
    messages = {row['id']:row for row in detail(client,ids[0]).json()['messages']}
    assert messages[first]['referral'] == {'headline':'First advertisement'}
    assert messages[second]['referral'] == {'headline':'Second advertisement'}
    assert messages[third]['referral'] is None and messages[outbound]['referral'] is None
    response = client.get(f'/crm/whatsapp/api/conversations/{ids[0]}?limit=1')
    assert [row['id'] for row in response.json()['messages']] == [outbound]
    assert 'Other customer' not in response.text and 'First advertisement' not in response.text


def test_staff_authentication_is_required(inbox):
    client,_,ids,add,_ = inbox
    add({'headline':'Staff only'})
    anonymous = TestClient(main.app,base_url='https://testserver')
    assert anonymous.get(f'/crm/whatsapp/api/conversations/{ids[0]}',follow_redirects=False).status_code == 303
    assert detail(client,ids[0]).json()['messages'][0]['referral']['headline'] == 'Staff only'


def test_referral_does_not_replace_audio_or_attachment_metadata(inbox):
    client,_,ids,add,_ = inbox
    audio = add({'headline':'Audio referral'},media='legacy-audio')
    image = add({'headline':'Image referral'},attachment={'media_id':'image','media_type':'image','mime_type':'image/png'})
    messages = {row['id']:row for row in detail(client,ids[0]).json()['messages']}
    assert messages[audio]['message_type'] == 'audio' and messages[audio]['media_url'].endswith(f'/messages/{audio}/media')
    assert messages[image]['attachments'][0]['type'] == 'image'
    assert messages[audio]['referral']['headline'] == 'Audio referral'
    assert messages[image]['referral']['headline'] == 'Image referral'


def test_chromium_cards_escape_html_keep_message_links_and_preserve_media(inbox,monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api',reason='Optional Playwright browser tooling unavailable')
    chromium = shutil.which('chromium') or shutil.which('chromium-browser')
    if not chromium: pytest.skip('Chromium executable unavailable')
    client,_,ids,add,_ = inbox
    first = add({'headline':'<img src=x onerror="window.injected=true">','body':'<script>window.injected=true</script>',
                 'source_type':'ad','source_url':'https://fb.me/safe-ad','ctwa_clid':'private-browser-click',
                 'thumbnail_url':'https://attacker.test/track.png'})
    second = add({'headline':'Second advertisement','source_type':'post','source_url':'javascript:alert(1)'})
    plain = add()
    audio = add({'headline':'Voice referral'},media='voice')
    image = add({'headline':'Image referral'},attachment={'media_id':'image','media_type':'image','mime_type':'image/png'})
    monkeypatch.setattr(main,'download_whatsapp_attachment',lambda *args:(PNG,'image/png'))
    monkeypatch.setattr(main,'download_whatsapp_media',lambda *args:(b'OggS-test','audio/ogg'))
    requests = []
    with playwright.sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width':1366,'height':900})
            errors = [];page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                parsed = urlsplit(route.request.url)
                requests.append(route.request.url)
                if parsed.hostname != 'inbox.test':
                    route.fulfill(status=200,content_type='application/javascript',body='');return
                response = client.request(route.request.method,parsed.path+('?' + parsed.query if parsed.query else ''),follow_redirects=False)
                headers = {key:value for key,value in response.headers.items() if key.lower() not in {'content-length','content-encoding','set-cookie'}}
                route.fulfill(status=response.status_code,headers=headers,body=response.content)
            page.route('**/*',serve)
            page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[0]}')
            page.locator('.ad-referral').first.wait_for()
            assert page.locator('.ad-referral').count() == 4
            for mid in (first,second,audio,image):
                assert page.locator(f'[data-message-id="{mid}"] > .ad-referral + .bubble').count() == 1
            assert page.locator(f'[data-message-id="{plain}"] .ad-referral').count() == 0
            assert page.locator('.ad-referral img,.ad-referral video,.ad-referral script').count() == 0
            assert page.evaluate('window.injected') is None
            assert '<img src=x' in page.locator(f'[data-message-id="{first}"] .ad-referral').inner_text()
            safe_link = page.locator(f'[data-message-id="{first}"] .ad-referral a')
            assert safe_link.get_attribute('href') == 'https://fb.me/safe-ad'
            assert safe_link.get_attribute('rel') == 'noopener noreferrer'
            assert safe_link.get_attribute('referrerpolicy') == 'no-referrer'
            assert page.locator(f'[data-message-id="{second}"] .ad-referral a').count() == 0
            assert 'private-browser-click' not in page.content()
            assert not any('attacker.test' in request or 'fb.me/safe-ad' in request for request in requests)
            page.wait_for_function("document.querySelector('.message-attachment img').naturalWidth > 0")
            page.evaluate("window.savedMedia=[document.querySelector('.audio-bubble audio'),document.querySelector('.message-attachment img')]")
            page.wait_for_timeout(4500)
            assert page.evaluate('window.savedMedia.every(node=>node.isConnected)')
            assert page.locator(f'[data-message-id="{first}"] > .ad-referral + .bubble').count() == 1
            page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(250)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert errors == []
        finally:
            browser.close()
