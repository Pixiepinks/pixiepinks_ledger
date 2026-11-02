"""Match working Saved Voice input without changing library sends or calling Meta."""
import pytest
import audio_service as audio
import main
import voice_diagnostic_tools as tools
import voice_packet_analysis as bits
import voice_trace
from models import WhatsAppManualSend,GlobalAIReplyControl
from test_voice_audio_compatibility import encoded
from test_saved_voices import voices
from test_whatsapp_quick_messages import inbox


@pytest.mark.parametrize('codec,container,mime',[('libopus','webm','audio/webm'),('aac','ipod','audio/mp4'),('libopus','ogg','audio/ogg')])
def test_recording_formats_use_validated_ogg_before_final_ffmpeg(voices,monkeypatch,codec,container,mime):
    v=voices;raw=encoded(codec,container,2)
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    monkeypatch.setattr(main,'send_whatsapp_audio',lambda *args,**kwargs:v.sends.append((args,kwargs)) or 'wamid.browser.'+container)
    with v.sessions() as db:db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
    response=v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/send-audio',
        files={'audio':('recorded.audio',raw,mime)},headers={'Idempotency-Key':'browser-staged-'+container})
    assert response.status_code==200,response.text
    assert len(v.uploads)==1
    assert v.sends[0][1]['voice'] is False
    with v.sessions() as db:
        trace=db.query(WhatsAppManualSend).filter_by(message_text='Voice message').one().audio_trace
        stages=trace['stages'];stage=stages['browser_ogg_staging']
        assert stage['source']==voice_trace.fingerprint(raw)
        assert stage['validated_format']['codec']=='opus'
        assert stage['output']==stages['ffmpeg_normalization']['source']
        assert stages['normalized_memory']==voice_trace.fingerprint(v.uploads[0])
        assert db.get(GlobalAIReplyControl,1).enabled is False
    reference=tools.encode(audio._normalize_pyav(raw,mime)[0])
    assert bits.compare(bits.profile(reference),bits.profile(v.uploads[0]))['codec_packets_equal']


def test_stage_failure_never_uploads_or_sends(voices,monkeypatch):
    v=voices;monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    response=v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/send-audio',
        files={'audio':('bad.webm',b'bad','audio/webm')},headers={'Idempotency-Key':'bad-browser-stage'})
    assert response.status_code==422 and not v.uploads and not v.sends


def test_final_failure_does_not_upload_staging_as_fallback(voices,monkeypatch):
    v=voices;raw=encoded('libopus','webm',1)
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    monkeypatch.setattr(tools,'production_encode',lambda raw:(_ for _ in ()).throw(ValueError('fixture failure')))
    response=v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/send-audio',
        files={'audio':('recorded.webm',raw,'audio/webm')},headers={'Idempotency-Key':'failed-browser-final'})
    assert response.status_code==422 and not v.uploads and not v.sends


def test_off_does_not_change_original_browser_input(monkeypatch):
    monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','false')
    monkeypatch.setattr(audio,'_normalize_pyav',lambda *a:pytest.fail('OFF added staging'))
    assert audio.stage_browser_voice(b'original','audio/webm')==b'original'


def test_real_chromium_mediarecorder_bytes_use_staged_pipeline(voices,monkeypatch):
    import base64
    import shutil
    api=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=chromium,headless=True,args=[
            '--no-sandbox','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream'])
        try:
            page=browser.new_page()
            page.route('**/*',lambda route:route.fulfill(status=200,body='<html>Local generated audio only</html>',content_type='text/html'))
            page.goto('https://browser-voice.test')
            recording=page.evaluate('''async () => {
                const stream=await navigator.mediaDevices.getUserMedia({audio:true});
                const mime=['audio/ogg;codecs=opus','audio/webm;codecs=opus','audio/webm','audio/mp4'].find(t=>MediaRecorder.isTypeSupported(t));
                const recorder=new MediaRecorder(stream,{mimeType:mime}), chunks=[];
                recorder.ondataavailable=e=>{if(e.data.size) chunks.push(e.data)};
                const stopped=new Promise(resolve=>recorder.onstop=resolve);
                recorder.start(1000); await new Promise(resolve=>setTimeout(resolve,1400)); recorder.stop(); await stopped;
                stream.getTracks().forEach(t=>t.stop());
                const blob=new Blob(chunks,{type:recorder.mimeType});
                const data=await new Promise(resolve=>{const r=new FileReader();r.onload=()=>resolve(r.result.split(',')[1]);r.readAsDataURL(blob)});
                return {mime:blob.type,data};
            }''')
        finally:browser.close()
    v=voices;monkeypatch.setenv('VOICE_FFMPEG_ENCODER_ENABLED','true')
    monkeypatch.setattr(main,'send_whatsapp_audio',lambda *a,**kw:'wamid.browser.chromium')
    result=v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/send-audio',
        files={'audio':('chrome.webm',base64.b64decode(recording['data']),recording['mime'])},
        headers={'Idempotency-Key':'chromium-real-generated-recording'})
    assert result.status_code==200,result.text
    assert audio.validate_voice_audio(v.uploads[0])['codec']=='opus'
