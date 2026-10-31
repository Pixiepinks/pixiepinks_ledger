"""Authorized staff rollout; provider requests remain mocked."""
import pytest
import main
import audio_service
import voice_diagnostic_tools as tools
import voice_packet_analysis as bits
from models import SavedVoice, SavedVoiceSend,GlobalAIReplyControl
from test_saved_voices import voices,wav
from test_whatsapp_quick_messages import inbox


def test_staff_variant_c_reencodes_retained_source_preserves_library_and_playback(voices,monkeypatch):
    if not tools.runtime().get('verified'):pytest.skip('Pinned FFmpeg not installed')
    v=voices;row=v.create().json()['voice']
    originals={p.name:p.read_bytes() for p in v.directory.iterdir()}
    with v.sessions() as db:
        db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    assert v.send(row).json()['status']=='SENT'
    assert v.send(row).json()['status']=='SENT'
    assert len(v.uploads)==len(v.sends)==1
    assert v.sends[0][1]=={'voice':False,'return_outcome':True}
    comparison=bits.compare(bits.profile(tools.encode(wav())),bits.profile(v.uploads[0]))
    assert comparison['codec_packets_equal']
    assert all((v.directory/name).read_bytes()==value for name,value in originals.items())
    with v.sessions() as db:
        attempt=db.query(SavedVoiceSend).one();mid=attempt.message_id;wid=attempt.whatsapp_message_id
        assert attempt.audio_trace['staff_audio_profile']=='variant_c_ordinary'
        assert db.get(SavedVoice,row['id']).revision==row['revision']
        assert db.get(GlobalAIReplyControl,1).enabled is False
    preview=v.client.get(f'/crm/whatsapp/api/conversations/{v.ids[0]}/messages/{mid}/media')
    assert preview.status_code==200 and preview.content==v.uploads[0]
    monkeypatch.setattr(main.settings,'META_APP_SECRET',None)
    payload={'entry':[{'changes':[{'value':{'statuses':[{'id':wid,'status':'failed','errors':[
        {'code':131053,'title':'Media upload error','error_data':{'details':'Cannot process this stream'}}]}]}}]}]}
    assert v.client.post('/webhook',json=payload).status_code==200
    with v.sessions() as db:
        error=db.query(SavedVoiceSend).one().audio_trace['delivery_failure']['errors'][0]
        assert error['code']==131053 and error['error_data']['details']=='Cannot process this stream'


def test_browser_variant_c_requests_ordinary_audio(voices,monkeypatch):
    v=voices;calls=[]
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    monkeypatch.setattr(main,'send_whatsapp_audio',lambda *a,**kw:calls.append(kw) or 'wamid.staff.c')
    result=v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/send-audio',
        files={'audio':('recording.wav',wav(),'audio/wav')},
        headers={'X-CSRF-Token':v.csrf,'Idempotency-Key':'staff-variant-c-browser-01'})
    assert result.status_code==200,result.text
    assert calls==[{'voice':False}]


def test_variant_c_conversion_failure_does_not_upload_or_mutate_library(voices,monkeypatch):
    v=voices;row=v.create().json()['voice']
    originals={p.name:p.read_bytes() for p in v.directory.iterdir()}
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    monkeypatch.setattr(tools,'production_encode',lambda raw:(_ for _ in ()).throw(ValueError('demux failure')))
    assert v.send(row).status_code==422
    assert not v.uploads and not v.sends
    assert originals=={p.name:p.read_bytes() for p in v.directory.iterdir()}
