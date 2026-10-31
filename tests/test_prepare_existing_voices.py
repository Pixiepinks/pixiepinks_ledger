"""Explicit preparation preserves legacy recordings; Meta calls are mocked."""
import pytest
import audio_service
import voice_diagnostic_tools as tools
import saved_voice_service as service
from models import SavedVoice,GlobalAIReplyControl
from test_voice_audio_compatibility import legacy,encoded
from test_saved_voices import voices,wav
from test_whatsapp_quick_messages import inbox


@pytest.mark.parametrize('format',['m4a','ogg','wav'])
def test_explicit_prepare_preserves_metadata_originals_and_enables_send(voices,monkeypatch,format):
    v=voices
    raw=encoded(seconds=10.56) if format=='m4a' else audio_service._normalize_pyav(wav())[0] if format=='ogg' else wav()
    vid,name=legacy(v,raw)
    with v.sessions() as db:
        db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
        row=db.get(SavedVoice,vid);metadata=(row.title,row.category,row.transcript,row.language)
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    assert v.send({'id':vid,'revision':1}).status_code==409
    assert not v.uploads and not v.sends
    response=v.client.post(f'/crm/saved-voices/api/{vid}/prepare',headers={'X-CSRF-Token':v.csrf})
    assert response.status_code==200,response.text
    current=response.json()['voice']
    assert current['ready_to_send'] and current['revision']==2
    assert not v.uploads and not v.sends
    with v.sessions() as db:
        row=db.get(SavedVoice,vid)
        assert (row.title,row.category,row.transcript,row.language)==metadata
        assert row.original_storage_name==name
        assert service._read_file(name)==raw
        assert db.get(GlobalAIReplyControl,1).enabled is False
    preview=v.client.get(f'/crm/saved-voices/api/{vid}/audio')
    assert preview.status_code==200 and audio_service.validate_voice_audio(preview.content)['codec']=='opus'
    assert v.client.post(f'/crm/saved-voices/api/{vid}/prepare',headers={'X-CSRF-Token':v.csrf}).json()['voice']['revision']==2
    assert v.send(current).json()['status']=='SENT' and len(v.uploads)==1


@pytest.mark.parametrize('failure',['corrupt','encoder'])
def test_failed_prepare_preserves_original_and_sends_nothing(voices,monkeypatch,failure):
    v=voices;raw=b'bad recording' if failure=='corrupt' else wav();vid,name=legacy(v,raw)
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    if failure=='encoder':monkeypatch.setattr(tools,'production_encode',lambda raw:(_ for _ in ()).throw(ValueError('fixture failure')))
    result=v.client.post(f'/crm/saved-voices/api/{vid}/prepare',headers={'X-CSRF-Token':v.csrf})
    assert result.status_code==422
    assert service._read_file(name)==raw and not v.uploads and not v.sends
    with v.sessions() as db:
        row=db.get(SavedVoice,vid);assert row.processing_status=='FAILED' and row.revision==1
