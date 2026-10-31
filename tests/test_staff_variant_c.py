"""Authorized staff rollout; provider requests remain mocked."""
import pytest
import main
import audio_service
import voice_diagnostic_tools as tools
import voice_packet_analysis as bits
from models import SavedVoice, SavedVoiceSend,GlobalAIReplyControl
from test_saved_voices import voices,wav
from test_whatsapp_quick_messages import inbox


def test_staff_variant_c_reencodes_validated_ogg_preserves_library_and_playback(voices,monkeypatch):
    if not tools.runtime().get('verified'):pytest.skip('Pinned FFmpeg not installed')
    v=voices;row=v.create().json()['voice']
    originals={p.name:p.read_bytes() for p in v.directory.iterdir()}
    with v.sessions() as db:
        stored=db.get(SavedVoice,row['id']);stored_input=(v.directory/stored.storage_name).read_bytes()
    with v.sessions() as db:
        db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    assert v.send(row).json()['status']=='SENT'
    assert v.send(row).json()['status']=='SENT'
    assert len(v.uploads)==len(v.sends)==1
    assert v.sends[0][1]=={'voice':False,'return_outcome':True}
    comparison=bits.compare(bits.profile(tools.encode(stored_input)),bits.profile(v.uploads[0]))
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


def test_ready_m4a_upload_sends_from_validated_ogg_not_nonseekable_original(voices,monkeypatch):
    from test_voice_audio_compatibility import encoded
    v=voices;source=encoded(seconds=10.56)
    assert source.index(b'moov')>source.index(b'mdat') and len(source)>32768
    with pytest.raises(ValueError):tools.encode(source)
    response=v.client.post('/crm/saved-voices/api',data={'title':'M4A original','transcript':'Owner fixture','language':'singlish','category':'Test'},
        files={'audio':('original.m4a',source,'audio/mp4')},headers={'X-CSRF-Token':v.csrf})
    assert response.status_code==201,response.text
    row=response.json()['voice'];before={p.name:p.read_bytes() for p in v.directory.iterdir()}
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    result=v.send(row)
    assert result.status_code==200 and result.json()['status']=='SENT',result.text
    assert len(v.uploads)==1 and v.sends[0][1]['voice'] is False
    assert all((v.directory/name).read_bytes()==raw for name,raw in before.items())


def test_browser_shows_422_error_without_clearing_it_or_retrying(voices,monkeypatch):
    import shutil
    from urllib.parse import urlsplit
    import saved_voice_service
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    v=voices;v.create()
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    monkeypatch.setattr(saved_voice_service,'normalize_voice_audio',lambda *a:(_ for _ in ()).throw(audio_service.VoiceAudioError('Fixture conversion rejected')))
    calls=[]
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page()
            def serve(route):
                req=route.request;url=urlsplit(req.url)
                if url.hostname!='staff-voices.test':route.fulfill(status=200,body='');return
                if req.method=='POST' and url.path.endswith('/send-saved-voice'):calls.append(url.path)
                response=v.client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,
                    headers={k:value for k,value in req.headers.items() if k in {'content-type','x-csrf-token','idempotency-key','range'}},follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:value for k,value in response.headers.items() if k in {'content-type','content-range','accept-ranges','cache-control'}})
            page.route('**/*',serve)
            page.goto(f'https://staff-voices.test/crm/whatsapp?conversation_id={v.ids[0]}')
            page.locator(f'[data-conversation-id="{v.ids[0]}"]').click();page.locator('#saved-voices-open').click()
            page.get_by_role('button',name='Choose recording',exact=True).click()
            page.locator('#saved-voice-send').click()
            api.expect(page.locator('#saved-voice-status')).to_have_text('Nothing was sent. Fixture conversion rejected')
            api.expect(page.locator('#saved-voice-send')).to_be_disabled()
            assert len(calls)==1 and not v.uploads and not v.sends
        finally:browser.close()
