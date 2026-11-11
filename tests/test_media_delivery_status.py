"""Signed asynchronous media failure diagnostics; mocked uploads, no real sends."""
import hashlib
import hmac
import io
import json
from datetime import datetime
import av
import pytest
import main
import outbound_media_service as service
from models import WhatsAppMediaSend, WhatsAppConversationMessage
from media_delivery_status import MediaDeliveryStatus
from test_whatsapp_outbound_media import inbox


def callback(f,monkeypatch,status='failed',code=131053,when=None):
    secret='isolated-media-delivery-secret';monkeypatch.setattr(main.settings,'META_APP_SECRET',secret)
    data={'id':'wamid.actual-media','status':status,'timestamp':str(when or int(datetime.utcnow().timestamp())),
        'errors':[{'code':code,'error_subcode':99,'message':'PRIVATE_TOKEN must not be retained','error_data':{'details':'https://private.example/token'}}]}
    raw=json.dumps({'entry':[{'changes':[{'value':{'statuses':[data]}}]}]}).encode()
    signature='sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    return f[0].post('/webhook',content=raw,headers={'Content-Type':'application/json','X-Hub-Signature-256':signature})


def test_failed_delivery_code_is_persisted_and_visible_without_resending(inbox,monkeypatch):
    f=inbox;assert f[3]().json()['status']=='SENT'
    assert callback(f,monkeypatch).status_code==200
    assert callback(f,monkeypatch).status_code==200
    with f[1]() as db:
        row=db.query(MediaDeliveryStatus).one();assert row.status=='failed' and row.error_code==131053
        assert db.query(WhatsAppMediaSend).one().status=='SENT'  # Accepted claim never reset for retry.
    response=f[0].get(f'/crm/whatsapp/api/conversations/{f[2][0]}')
    item=response.json()['messages'][0]
    assert item['status']=='failed' and item['delivery_failure']['code']==131053
    assert 'PRIVATE_TOKEN' not in response.text and 'private.example' not in response.text
    state=f[0].get(f'/crm/whatsapp/api/conversations/{f[2][0]}/media-sends/safe-idempotency-key-1234').json()
    assert state['status']=='SENT' and state['delivery_status']=='failed' and state['retry_safe'] is False
    assert f[3]().json()['delivery_status']=='failed' and len(f[5])==1


def test_stale_status_cannot_hide_failure_and_read_can_confirm_delivery(inbox,monkeypatch):
    f=inbox;f[3]();when=int(datetime.utcnow().timestamp())
    assert callback(f,monkeypatch,when=when).status_code==200
    assert callback(f,monkeypatch,status='sent',when=when-1).status_code==200
    with f[1]() as db:assert db.query(WhatsAppConversationMessage).one().send_status=='failed'
    assert callback(f,monkeypatch,status='read',when=when+2).status_code==200
    assert callback(f,monkeypatch,when=when+1).status_code==200
    item=f[0].get(f'/crm/whatsapp/api/conversations/{f[2][0]}').json()['messages'][0]
    assert item['status']=='read' and item['delivery_failure'] is None


def fragmented_mp4():
    buffer=io.BytesIO()
    with av.open(buffer,'w',format='mp4',options={'movflags':'frag_keyframe+empty_moov+default_base_moof'}) as target:
        stream=target.add_stream('libx264',rate=24);stream.width=32;stream.height=32;stream.pix_fmt='yuv420p';stream.thread_count=1
        for n in range(24):
            frame=av.VideoFrame(32,32,'rgb24');frame.planes[0].update(bytes(frame.planes[0].buffer_size));frame.pts=n
            for packet in stream.encode(frame):target.mux(packet)
        for packet in stream.encode():target.mux(packet)
    return buffer.getvalue()


def test_browser_fragmented_mp4_gets_canonical_output(inbox):
    raw=fragmented_mp4();assert b'moof' in raw
    response=inbox[3](name='recorded-video.mp4',raw=raw)
    assert response.status_code==200 and response.json()['status']=='SENT',response.text
    output,data=inbox[4][0]
    assert output!=raw and b'moof' not in output
    assert output.index(b'moov')<output.index(b'mdat')
    with av.open(io.BytesIO(output)) as source:
        video=source.streams.video[0]
        assert video.codec_context.name=='h264' and 'Baseline' in video.codec_context.profile
        assert video.codec_context.format.name=='yuv420p'


def test_additive_migration_preserves_existing_accepted_send(inbox,monkeypatch):
    import database
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    f=inbox;f[3]();engine=f[1].kw['bind']
    MediaDeliveryStatus.__table__.drop(engine)
    monkeypatch.setattr(database,'engine',engine)
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.stamp(config,'20261111_40');command.upgrade(config,'head');command.upgrade(config,'head')
    with f[1]() as db:
        assert db.query(WhatsAppMediaSend).one().status=='SENT'
        assert db.query(WhatsAppConversationMessage).count()==1 and db.query(MediaDeliveryStatus).count()==0


def test_postgresql_status_upsert_and_additive_migration(monkeypatch):
    import os
    from pathlib import Path
    from uuid import uuid4
    from datetime import timedelta
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.engine import make_url
    from alembic import command
    from alembic.config import Config
    import database
    from models import Base
    from media_delivery_status import record
    url=os.getenv('TEST_POSTGRESQL_URL')
    if not url:pytest.skip('Disposable TEST_POSTGRESQL_URL not configured')
    parsed=make_url(url);assert parsed.host in {'127.0.0.1','localhost'} and parsed.database=='pixiepinks_test'
    schema='media_status_'+uuid4().hex;admin=create_engine(url)
    with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'})
    try:
        Base.metadata.create_all(engine);MediaDeliveryStatus.__table__.drop(engine)
        monkeypatch.setattr(database,'engine',engine)
        config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        command.stamp(config,'20261111_40');command.upgrade(config,'head');command.upgrade(config,'head')
        sessions=sessionmaker(bind=engine)
        with sessions() as db:
            message=WhatsAppConversationMessage(phone_number='94770000000',direction='outbound',message_text='Video',response_kind='staff_media',whatsapp_message_id='wamid.pg-status',send_status='sent')
            db.add(message);db.commit();mid=message.id;now=datetime.utcnow()
            record(db,'wamid.pg-status','failed',[{'code':131053}],now);db.commit()
            record(db,'wamid.pg-status','sent',[],now-timedelta(seconds=1));db.commit()
            assert db.get(WhatsAppConversationMessage,mid).send_status=='failed'
            assert db.query(MediaDeliveryStatus).one().error_code==131053
            record(db,'wamid.pg-status','read',[],now+timedelta(seconds=1));db.commit()
            assert db.get(WhatsAppConversationMessage,mid).send_status=='read'
    finally:
        engine.dispose()
        with admin.begin() as conn:conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
