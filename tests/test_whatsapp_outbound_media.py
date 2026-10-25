"""Isolated staff-media API, durable idempotency and browser regressions."""
import hashlib
import hmac
import io
import json
import shutil
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import Mock
from urllib.parse import urlsplit

import av
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

import main
import outbound_media_service as service
from models import Base, User, WhatsAppConversation, WhatsAppConversationMessage, WhatsAppMessageAttachment, WhatsAppMediaSend
from utils_auth import hash_password


def png():
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff\0\0' * 2) * 2)) + chunk(b'IEND', b'')


def jpeg():
    frame = av.VideoFrame(16, 16, 'rgb24')
    frame.planes[0].update(bytes(frame.planes[0].buffer_size))
    output = io.BytesIO()
    with av.open(output, 'w', format='image2pipe') as target:
        stream = target.add_stream('mjpeg', rate=1); stream.width = 16; stream.height = 16; stream.pix_fmt = 'yuvj420p'
        for packet in stream.encode(frame): target.mux(packet)
        for packet in stream.encode(): target.mux(packet)
    return output.getvalue()


def pdf():
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
    output = io.BytesIO(); writer.write(output); return output.getvalue()


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path}/media.db', connect_args={'check_same_thread': False, 'timeout': 20})
    @event.listens_for(engine, 'connect')
    def foreign_keys(conn, _): conn.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    def get_db():
        with sessions() as db: yield db
    original = dict(main.app.dependency_overrides); main.app.dependency_overrides[main.get_db] = get_db
    monkeypatch.setattr(main, 'SessionLocal', sessions)
    with sessions() as db:
        db.add_all([User(username='media-staff', password_hash=hash_password('password'), role='STAFF'),
                    User(username='other-staff', password_hash=hash_password('password'), role='STAFF')])
        conversations = [WhatsAppConversation(phone_number=f'9477000000{n}', customer_name=f'Customer {n}',
            mode=mode, last_customer_message_at=datetime.utcnow(), unread_count=2) for n, mode in [(1,'HUMAN'), (2,'AI')]]
        db.add_all(conversations); db.commit(); ids = [c.id for c in conversations]
    client = TestClient(main.app, base_url='https://testserver')
    client.post('/login', data={'username':'media-staff', 'password':'password'})
    policy = client.get('/crm/whatsapp/api/media-policy').json()
    uploaded, sent = [], []
    def upload(raw, data): uploaded.append((raw, data)); return 'meta-uploaded-media'
    def send(phone, media_id, data): sent.append((phone, media_id, data)); return 'SENT', 'wamid.actual-media'
    monkeypatch.setattr(service, 'upload_media', upload); monkeypatch.setattr(service, 'send_media', send)
    def post(name='photo.png', raw=None, caption='Photo caption', key='safe-idempotency-key-1234', cid=None, **kwargs):
        return client.post(f'/crm/whatsapp/api/conversations/{cid or ids[0]}/send-media',
            files={'file': (name, png() if raw is None else raw, kwargs.pop('mime','application/octet-stream'))},
            data={'caption': caption}, headers={'X-CSRF-Token': policy['csrf_token'], 'Idempotency-Key': key}, **kwargs)
    yield client, sessions, ids, post, uploaded, sent
    main.app.dependency_overrides.clear(); main.app.dependency_overrides.update(original); engine.dispose()


@pytest.mark.parametrize('name,content,kind,mime', [('photo.png',png,'image','image/png'), ('photo.jpg',jpeg,'image','image/jpeg'), ('photo.jpeg',jpeg,'image','image/jpeg'), ('invoice.pdf',pdf,'document','application/pdf')])
def test_upload_send_persistence_and_timeline(inbox, name, content, kind, mime):
    client, sessions, ids, post, uploads, sends = inbox
    response = post(name, content(), caption='  <caption>  ')
    assert response.status_code == 200 and response.json()['status'] == 'SENT'
    assert len(uploads) == len(sends) == 1
    with sessions() as db:
        message = db.query(WhatsAppConversationMessage).one(); attachment = db.query(WhatsAppMessageAttachment).one()
        assert message.direction == 'outbound' and message.whatsapp_message_id == 'wamid.actual-media'
        assert message.media_id is None and message.send_status == 'sent' and message.sent_by == 'media-staff'
        assert attachment.message_id == message.id and attachment.media_id == 'meta-uploaded-media'
        assert (attachment.media_type, attachment.mime_type, attachment.filename, attachment.caption) == (kind,mime,name,'<caption>')
        assert attachment.sha256 == hashlib.sha256(content()).hexdigest()
        conversation = db.get(WhatsAppConversation, ids[0]); assert conversation.mode == 'HUMAN' and conversation.unread_count == 2
    payload = client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()['messages'][0]
    assert payload['attachments'][0]['type'] == kind and payload['source'] == 'staff_media'
    assert 'meta-uploaded-media' not in json.dumps(payload)
    main_download = Mock(return_value=(content(), mime))
    # Existing preview route remains the only retriever and obtains fresh Meta URLs.
    import unittest.mock
    with unittest.mock.patch.object(main, 'download_whatsapp_attachment', main_download):
        fetched = client.get(payload['attachments'][0]['url'])
    assert fetched.status_code == 200 and fetched.content == content()
    assert fetched.headers['cache-control'] == 'private, no-store'


@pytest.mark.parametrize('name,raw', [('evil.exe',b'x'), ('photo.jpg',png()), ('photo.png',b'<html>bad</html>'), ('invoice.pdf',b'%PDF-1.7 fake %%EOF'), ('empty.png',b''), ('file.svg',b'<svg/>')])
def test_invalid_content_never_reaches_meta(inbox,name,raw):
    _,sessions,_,post,uploads,sends = inbox
    assert post(name,raw).status_code in {413,422}
    assert not uploads and not sends
    with sessions() as db: assert db.query(WhatsAppMediaSend).count() == db.query(WhatsAppConversationMessage).count() == 0


def test_size_caption_filename_and_processing_limits(inbox,monkeypatch):
    _,_,_,post,uploads,_ = inbox
    monkeypatch.setattr(service.settings,'WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES',1024)
    assert post(raw=png()+bytes(1024)).status_code == 413
    assert post(caption='x'*1025).status_code == 422
    assert post('../folder/photo.png').status_code == 200
    assert uploads[0][1]['filename'] == 'photo.png'
    monkeypatch.setattr(service.subprocess,'run',Mock(side_effect=service.subprocess.TimeoutExpired('validator',10)))
    assert post(key='another-safe-request-12345').status_code == 422


def test_extreme_dimensions_and_encrypted_pdf(inbox):
    _,_,_,post,_,_ = inbox
    raw = png(); header = struct.pack('>IIBBBBB',100000,100000,8,2,0,0,0)
    raw = raw[:16]+header+struct.pack('>I',zlib.crc32(b'IHDR'+header))+raw[33:]
    assert post(raw=raw).status_code == 422
    writer = PdfWriter(); writer.add_blank_page(width=10,height=10); writer.encrypt('secret'); out=io.BytesIO(); writer.write(out)
    assert post('encrypted.pdf',out.getvalue()).status_code == 422


def test_auth_authorization_csrf_and_scope(inbox):
    client,sessions,ids,post,uploads,_ = inbox
    path=f'/crm/whatsapp/api/conversations/{ids[0]}/send-media'
    anonymous=TestClient(main.app,base_url='https://testserver')
    assert anonymous.post(path,follow_redirects=False).status_code == 303
    assert client.post(path,files={'file':('photo.png',png())}).status_code == 403
    assert not uploads
    assert post().json()['status']=='SENT'
    assert post(cid=ids[1]).status_code == 409
    assert post(raw=jpeg(),name='other.jpg').status_code == 409
    key='safe-idempotency-key-1234'
    assert client.get(f'/crm/whatsapp/api/conversations/{ids[1]}/media-sends/{key}').status_code==409
    client.post('/login',data={'username':'other-staff','password':'password'})
    assert client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/media-sends/{key}').status_code==409
    with sessions() as db:
        db.query(User).filter_by(username='other-staff').one().role='UNKNOWN'; db.commit()
    assert client.get('/crm/whatsapp/api/media-policy').status_code == 403


def test_duplicate_and_concurrent_claims(inbox):
    client,sessions,ids,post,uploads,sends=inbox
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses=list(executor.map(lambda _:post(),range(2)))
    assert all(r.status_code==200 for r in responses)
    assert len(uploads)==len(sends)==1
    assert post().json()['status']=='SENT'
    with sessions() as db:
        assert db.query(WhatsAppConversationMessage).count()==db.query(WhatsAppMessageAttachment).count()==db.query(WhatsAppMediaSend).count()==1
    assert client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/media-sends/safe-idempotency-key-1234').json()['status']=='SENT'


@pytest.mark.parametrize('result', ['UPLOAD_FAILED','SEND_FAILED','OUTCOME_UNCERTAIN'])
def test_provider_failures_are_not_success_and_never_auto_retry(inbox,monkeypatch,result):
    _,sessions,_,post,uploads,sends=inbox
    if result=='UPLOAD_FAILED': monkeypatch.setattr(service,'upload_media',lambda *a:None)
    else: monkeypatch.setattr(service,'send_media',lambda *a:(result,None))
    assert post().json()['status']==result
    assert post().json()['status']==result
    with sessions() as db:
        assert db.query(WhatsAppConversationMessage).count()==0
        assert db.query(WhatsAppMessageAttachment).count()==0
        assert db.query(WhatsAppMediaSend).one().status==result
    if result!='UPLOAD_FAILED': assert len(uploads)==1


def test_database_failure_after_remote_acceptance_blocks_resend(inbox,monkeypatch):
    _,sessions,_,post,uploads,sends=inbox
    original=sessions.class_.commit
    def commit(db):
        if any(isinstance(row,WhatsAppMediaSend) and row.status=='SENT' for row in db.dirty):
            raise OperationalError('safe-test',{},Exception('simulated'))
        return original(db)
    monkeypatch.setattr(sessions.class_,'commit',commit)
    response=post(); assert response.status_code==503 and response.json()['status']=='OUTCOME_UNCERTAIN'
    assert post().json()['status']=='SENDING'
    assert len(uploads)==len(sends)==1
    with sessions() as db: assert db.query(WhatsAppConversationMessage).count()==0 and db.query(WhatsAppMediaSend).one().status=='SENDING'


def test_modes_reply_window_and_delivery_status(inbox,monkeypatch):
    client,sessions,ids,post,_,_=inbox
    assert post(cid=ids[1]).json()['ok']
    with sessions() as db: assert db.get(WhatsAppConversation,ids[1]).mode=='AI'
    monkeypatch.setattr(main.settings,'META_APP_SECRET','isolated-test-secret')
    for status in ['delivered','read','failed']:
        body=json.dumps({'entry':[{'changes':[{'field':'messages','value':{'statuses':[{'id':'wamid.actual-media','status':status}]}}]}]}).encode()
        signature='sha256='+hmac.new(b'isolated-test-secret',body,hashlib.sha256).hexdigest()
        assert client.post('/webhook',content=body,headers={'X-Hub-Signature-256':signature}).status_code==200
        with sessions() as db: assert db.query(WhatsAppConversationMessage).one().send_status==status
    with sessions() as db:
        db.get(WhatsAppConversation,ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(hours=25);db.commit()
    assert post(key='closed-window-key-123456').status_code==409
    assert post(cid=ids[1]).json()['ok']  # Replay is valid even if reply window later closes.


def test_multipart_limit_and_busy(inbox,monkeypatch):
    client,_,ids,_,uploads,_=inbox
    policy=client.get('/crm/whatsapp/api/media-policy').json()
    headers={'X-CSRF-Token':policy['csrf_token'],'Idempotency-Key':'oversized-safe-key-1234','content-type':'multipart/form-data; boundary=test'}
    response=client.post(f'/crm/whatsapp/api/conversations/{ids[0]}/send-media',headers=headers,content=b'x'*(8*1024*1024+65537))
    assert response.status_code==413 and not uploads
    monkeypatch.setattr(main,'_outbound_media_slots',Mock(acquire=Mock(return_value=False)))
    assert client.post(f'/crm/whatsapp/api/conversations/{ids[0]}/send-media',headers=headers,content=b'').status_code==429


def test_meta_protocol_and_uncertain_response(monkeypatch,caplog):
    monkeypatch.setattr(service.settings,'META_WHATSAPP_ACCESS_TOKEN','private-test-token')
    monkeypatch.setattr(service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','123456')
    requests=[]
    def upstream(request):
        requests.append(request); payload={'id':'uploaded'} if request.url.path.endswith('/media') else {'messages':[{'id':'wamid.real'}]}; return service.httpx.Response(200,stream=service.httpx.ByteStream(json.dumps(payload).encode()))
    real_client=service.httpx.Client
    monkeypatch.setattr(service.httpx,'Client',lambda **kw:real_client(transport=service.httpx.MockTransport(upstream),**kw))
    data={'filename':'photo.png','mime_type':'image/png','media_type':'image','caption':'Caption'}
    assert service.upload_media(png(),data)=='uploaded'
    assert service.send_media('94770000001','uploaded',data)==('SENT','wamid.real')
    assert b'private-test-token' not in requests[0].content
    payload=json.loads(requests[1].content); assert payload['image']=={'id':'uploaded','caption':'Caption'}
    assert payload['type']=='image' and 'filename' not in payload['image']
    assert all(r.url.host=='graph.facebook.com' for r in requests)
    assert 'private-test-token' not in caplog.text and 'Caption' not in caplog.text
    monkeypatch.setattr(service,'_meta_request',lambda *a,**kw:(200,{'messages':[]}))
    assert service.send_media('94770000001','uploaded',data)==('OUTCOME_UNCERTAIN',None)
    monkeypatch.setattr(service,'_meta_request',lambda *a,**kw:(400,{'error':{'message':'private'}}))
    assert service.send_media('94770000001','uploaded',data)==('SEND_FAILED',None)


def test_chromium_selection_send_refresh_and_customer_switch(inbox):
    playwright=pytest.importorskip('playwright.sync_api')
    chromium=shutil.which('chromium')
    if not chromium: pytest.skip('Chromium unavailable')
    client,sessions,ids,_,uploads,sends=inbox
    with playwright.sync_playwright() as browser_api:
        browser=browser_api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1100,'height':850}); errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                request=route.request; parsed=urlsplit(request.url)
                if parsed.hostname!='inbox.test': route.fulfill(status=200,body='');return
                headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}}
                response=client.request(request.method,parsed.path+('?' + parsed.query if parsed.query else ''),headers=headers,
                    content=request.post_data_buffer,follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[0]}')
            page.wait_for_function('!document.getElementById("attachment-button").disabled')
            page.locator('#attachment-button').click()
            assert page.locator('#attachment-menu').is_visible()
            assert page.locator('#record-button').is_visible() and page.locator('#catalog-button').is_visible()
            page.locator('#attachment-file').set_input_files({'name':'photo.png','mimeType':'text/html','buffer':png()})
            assert page.locator('#attachment-preview img').is_visible()
            page.locator('#attachment-caption').fill('<caption> Staff photo')
            page.locator('#attachment-send').click()
            page.wait_for_function('document.getElementById("attachment-status").textContent.startsWith("Attachment sent")')
            assert len(uploads)==len(sends)==1
            page.reload();page.wait_for_function('!document.getElementById("attachment-button").disabled')
            page.locator('.message-attachment').wait_for()
            assert 'Staff photo' in page.locator('.message-attachment').inner_text()
            page.locator('#attachment-file').set_input_files({'name':'invoice.pdf','mimeType':'application/pdf','buffer':pdf()})
            assert 'invoice.pdf' in page.locator('#attachment-filename').inner_text()
            # Selecting another customer discards an unsubmitted file, never retargets it.
            page.locator('.conversation-row').filter(has_text='Customer 2').click()
            page.wait_for_function('(id)=>document.getElementById("inbox").dataset.activeConversation===String(id)',arg=ids[1])
            assert page.locator('#attachment-preview').is_hidden()
            assert len(sends)==1
            # Refresh restores an uncertain receipt without uploading or resending.
            with sessions() as db:
                user=db.query(User).filter_by(username='media-staff').one()
                db.add(WhatsAppMediaSend(idempotency_key='browser-uncertain-key-12345',conversation_id=ids[1],staff_id=user.id,fingerprint='0'*64,status='SENDING'));db.commit();uid=user.id
            page.evaluate('([uid,cid])=>sessionStorage.setItem(`whatsapp-media:${uid}:${cid}`,JSON.stringify({key:"browser-uncertain-key-12345"}))',[uid,ids[1]])
            page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[1]}')
            page.wait_for_function('!document.getElementById("attachment-check").hidden')
            assert page.locator('#attachment-send').is_disabled()
            assert 'not be sent again automatically' in page.locator('#attachment-status').inner_text()
            assert len(sends)==1 and not errors
            page.set_viewport_size({'width':375,'height':800})
            assert page.locator('#attachment-check').is_visible()
        finally:browser.close()


@pytest.mark.parametrize('orm_first',[False,True])
def test_additive_migration_and_postgresql_concurrent_send(tmp_path,monkeypatch,orm_first):
    import os
    from pathlib import Path
    from uuid import uuid4
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect,text
    from sqlalchemy.engine import make_url
    from sqlalchemy.schema import CreateSchema,DropSchema
    import database
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Dedicated TEST_POSTGRESQL_URL not configured')
    assert make_url(url).database=='pixiepinks_test'
    schema='outbound_'+uuid4().hex; admin=create_engine(url)
    with admin.begin() as conn:conn.execute(CreateSchema(schema))
    engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    sessions=sessionmaker(bind=engine)
    try:
        monkeypatch.setattr(database,'engine',engine)
        tables=[t for t in Base.metadata.sorted_tables if orm_first or t.name!='whatsapp_media_sends']
        Base.metadata.create_all(engine,tables=tables)
        with sessions() as db:
            conversation=WhatsAppConversation(phone_number='94771112222',mode='HUMAN',last_customer_message_at=datetime.utcnow())
            user=User(username='pg-staff',password_hash='not-a-login');db.add_all([conversation,user]);db.commit();cid,uid=conversation.id,user.id
            db.add(WhatsAppConversationMessage(phone_number=conversation.phone_number,direction='inbound',message_text='Preserved history'));db.commit()
        config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        command.stamp(config,'20261024_10');command.upgrade(config,'head');command.upgrade(config,'head')
        with engine.connect() as conn:assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='20261025_11'
        actual=inspect(engine)
        assert actual.get_unique_constraints('whatsapp_media_sends')[0]['column_names']==['idempotency_key']
        assert len(actual.get_foreign_keys('whatsapp_media_sends'))==3
        assert {c['name'] for c in actual.get_columns('whatsapp_media_sends')}==set(WhatsAppMediaSend.__table__.columns.keys())
        uploads=[];sends=[]
        monkeypatch.setattr(service,'upload_media',lambda *a:uploads.append(1) or 'pg-media-id')
        monkeypatch.setattr(service,'send_media',lambda *a:sends.append(1) and None or ('SENT','wamid.pg-real'))
        data=service.validate_file(png(),'photo.png','Caption')
        def worker(_):
            with sessions() as db:
                return service.perform_send(db,db.get(WhatsAppConversation,cid),db.get(User,uid),'pg-concurrent-safe-key',png(),data)
        with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(worker,range(2)))
        assert any(result['status']=='SENT' for result in results)
        assert len(uploads)==len(sends)==1
        assert worker(0)['status']=='SENT'
        with sessions() as db:
            assert db.query(WhatsAppConversationMessage).count()==2
            assert db.query(WhatsAppMessageAttachment).count()==db.query(WhatsAppMediaSend).count()==1
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(DropSchema(schema,cascade=True))
        admin.dispose()


@pytest.mark.parametrize('status,payload,expected',[(302,{},'OUTCOME_UNCERTAIN'),(503,{},'OUTCOME_UNCERTAIN'),(429,{'error':{'message':'secret upstream diagnostic'}},'SEND_FAILED'),(200,{'messages':[{'id':True}]},'OUTCOME_UNCERTAIN')])
def test_provider_rejection_redirect_and_malformed_ids(monkeypatch,status,payload,expected):
    monkeypatch.setattr(service,'_meta_request',lambda *a,**k:(status,payload))
    assert service.send_media('94770000001','uploaded',{'filename':'file.pdf','media_type':'document','caption':''})==(expected,None)


def test_meta_bounded_responses_timeouts_and_document_payload(monkeypatch):
    monkeypatch.setattr(service.settings,'META_WHATSAPP_ACCESS_TOKEN','private-token')
    monkeypatch.setattr(service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','123456')
    real_client=service.httpx.Client; captured=[]
    class LargeStream(service.httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(10):yield b'x'*8192
    def large(request):return service.httpx.Response(200,stream=LargeStream())
    monkeypatch.setattr(service.httpx,'Client',lambda **kw:real_client(transport=service.httpx.MockTransport(large),**kw))
    assert service._meta_request('media',data={})==(200,None)
    def timeout(request):raise service.httpx.ReadTimeout('private-token')
    monkeypatch.setattr(service.httpx,'Client',lambda **kw:real_client(transport=service.httpx.MockTransport(timeout),**kw))
    assert service._meta_request('media',data={})==(None,None)
    def record(route,**kwargs):captured.append(kwargs['json']);return 200,{'messages':[{'id':'wamid.doc'}]}
    monkeypatch.setattr(service,'_meta_request',record)
    assert service.send_media('94770000001','uploaded',{'filename':'invoice.pdf','media_type':'document','caption':'Invoice'})==('SENT','wamid.doc')
    assert captured[0]['document']=={'id':'uploaded','filename':'invoice.pdf','caption':'Invoice'}


def test_database_failure_before_send_does_not_contact_customer(inbox,monkeypatch):
    _,sessions,_,post,uploads,sends=inbox
    original=sessions.class_.commit
    def commit(db):
        if any(isinstance(row,WhatsAppMediaSend) and row.status=='SENDING' for row in db.dirty):
            raise OperationalError('safe-test',{},Exception('simulated'))
        return original(db)
    monkeypatch.setattr(sessions.class_,'commit',commit)
    assert post().status_code==503
    assert not sends and len(uploads)==1
    assert post().json()['status']=='UPLOADING'
    assert not sends and len(uploads)==1
