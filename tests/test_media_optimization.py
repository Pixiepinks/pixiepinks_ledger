"""Real local conversion with mocked WhatsApp upload/send; no live provider calls."""
import io
import struct
import zlib
from fractions import Fraction
import av
import pytest
import outbound_media_service as service
from models import User, ConversationShare, WhatsAppConversation as C
from test_whatsapp_outbound_media import inbox, png


def movie(seconds=20, codec='libx264', audio=False):
    output = io.BytesIO()
    with av.open(output, 'w', format='mov') as target:
        stream = target.add_stream(codec, rate=24); stream.width = 32; stream.height = 32
        stream.pix_fmt = 'yuv420p'; stream.thread_count = 1
        if codec == 'libx265': stream.options = {'x265-params': 'pools=none:frame-threads=1:log-level=error'}
        sound = target.add_stream('pcm_s16le', rate=48000) if audio else None
        if sound: sound.layout = 'mono'
        for n in range(seconds*24):
            frame = av.VideoFrame(32,32,'rgb24'); frame.planes[0].update(bytes(frame.planes[0].buffer_size)); frame.pts=n
            for packet in stream.encode(frame): target.mux(packet)
            if sound:
                af = av.AudioFrame(format='s16', layout='mono', samples=2000); af.sample_rate=48000; af.pts=n*2000; af.time_base=Fraction(1,48000)
                af.planes[0].update(bytes(af.planes[0].buffer_size))
                for packet in sound.encode(af): target.mux(packet)
        for packet in stream.encode(): target.mux(packet)
        if sound:
            for packet in sound.encode(): target.mux(packet)
    return output.getvalue()


def padded_png():
    data = b'comment\0'+b'x'*100_000; kind = b'tEXt'
    chunk = struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    raw=png(); return raw[:-12]+chunk+raw[-12:]


def test_twenty_second_phone_movie_converts_to_valid_whatsapp_mp4(inbox):
    client, sessions, ids, post, uploaded, sent = inbox
    response = post(name='phone.mov', raw=movie())
    assert response.status_code == 200 and response.json()['status'] == 'SENT', response.text
    raw, data = uploaded[0]
    assert data['filename'] == 'phone.mp4' and data['mime_type'] == 'video/mp4'
    assert 19.9 <= data['duration_seconds'] <= 20.1
    with av.open(io.BytesIO(raw)) as source:
        video=source.streams.video[0]
        assert video.codec_context.name == 'h264' and video.average_rate <= 30
        assert video.width <=1280 and video.height <=720
    assert len(raw) <= service.size_limit('video') and len(sent) == 1


def test_photo_above_output_limit_is_automatically_optimized(inbox, monkeypatch):
    monkeypatch.setattr(service.settings, 'WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES', 10_000)
    response = inbox[3](name='photo.png', raw=padded_png())
    assert response.status_code == 200 and response.json()['status'] == 'SENT', response.text
    raw, data = inbox[4][0]
    assert len(raw) < 10_000 and data['mime_type'] == 'image/jpeg' and data['filename'] == 'photo.jpg'
    assert response.json()['optimization']['sent_bytes'] < response.json()['optimization']['original_bytes']


def test_normal_compatible_photo_is_not_reencoded(inbox):
    response=inbox[3](raw=png()); assert response.json()['status']=='SENT'
    assert inbox[4][0][0]==png() and inbox[4][0][1]['filename']=='photo.png'


def test_oversized_or_invalid_source_never_uploads(inbox, monkeypatch):
    response=inbox[3](name='broken.mov',raw=b'not a movie')
    assert response.status_code==422 and not inbox[4] and not inbox[5]
    monkeypatch.setattr(service.settings,'WHATSAPP_OUTBOUND_MEDIA_SOURCE_MAX_BYTES',1024)
    monkeypatch.setattr(service.settings,'WHATSAPP_OUTBOUND_VIDEO_MAX_BYTES',1024)
    assert inbox[3](name='too-large.mp4',raw=b'x'*1025).status_code==413
    assert not inbox[4] and not inbox[5]


def test_staff_with_only_inbox_grant_can_send_owned_photo_and_video(inbox):
    client,sessions,ids,post,uploaded,sent=inbox
    with sessions() as db:
        user=db.query(User).filter_by(username='media-staff').one(); user.section_access=['whatsapp']; db.commit()
    assert post(key='staff-owned-photo-12345').json()['status']=='SENT'
    assert post(name='clip.mov',raw=movie(1),key='staff-owned-video-12345').json()['status']=='SENT'
    assert len(sent)==2


def test_shared_reply_permission_can_send_but_read_only_cannot(inbox):
    client,sessions,ids,post,uploaded,sent=inbox
    with sessions() as db:
        user=db.query(User).filter_by(username='media-staff').one()
        other=db.query(User).filter_by(username='other-staff').one()
        db.get(C,ids[0]).assigned_to_user_id=other.id
        db.add(ConversationShare(conversation_id=ids[0],user_id=user.id,can_reply=True,granted_by=other.id)); db.commit()
    assert post().json()['status']=='SENT'
    with sessions() as db: db.query(ConversationShare).one().can_reply=False; db.commit()
    assert post(key='shared-read-only-12345').status_code==403 and len(sent)==1


def test_hevc_phone_video_is_converted_to_h264(inbox):
    response=inbox[3](name='camera.mp4',raw=movie(1,codec='libx265'))
    assert response.status_code==200 and response.json()['status']=='SENT',response.text
    with av.open(io.BytesIO(inbox[4][0][0])) as source: assert source.streams.video[0].codec_context.name=='h264'


def test_movie_audio_is_converted_to_aac(inbox):
    response=inbox[3](name='with-sound.mov',raw=movie(1,audio=True))
    assert response.status_code==200 and response.json()['status']=='SENT',response.text
    with av.open(io.BytesIO(inbox[4][0][0])) as source: assert source.streams.audio[0].codec_context.name=='aac'


@pytest.mark.parametrize('change',['permission','window'])
def test_access_and_reply_window_are_rechecked_after_upload(inbox,monkeypatch,change):
    from datetime import datetime,timedelta
    client,sessions,ids,post,uploaded,sent=inbox
    def upload(raw,data):
        with sessions() as db:
            if change=='permission': db.query(User).filter_by(username='media-staff').one().section_access=[]
            else: db.get(C,ids[0]).last_customer_message_at=datetime.utcnow()-timedelta(hours=25)
            db.commit()
        return 'uploaded-but-not-sent'
    monkeypatch.setattr(service,'upload_media',upload)
    response=post()
    assert response.status_code==(403 if change=='permission' else 409) and not sent
