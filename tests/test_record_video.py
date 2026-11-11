"""Camera recording and browser-native WebM conversion; no live customer sends."""
import io
import shutil
from pathlib import Path
from urllib.parse import urlsplit
import av
import pytest
from test_whatsapp_outbound_media import inbox


def live_webm():
    output=io.BytesIO()
    with av.open(output,'w',format='webm',options={'live':'1'}) as target:
        stream=target.add_stream('libvpx',rate=24);stream.width=32;stream.height=32;stream.pix_fmt='yuv420p';stream.thread_count=1
        for n in range(24):
            frame=av.VideoFrame(32,32,'rgb24');frame.planes[0].update(bytes(frame.planes[0].buffer_size));frame.pts=n
            for packet in stream.encode(frame):target.mux(packet)
        for packet in stream.encode():target.mux(packet)
    return output.getvalue()


def test_browser_webm_without_duration_is_converted_to_mp4(inbox):
    data=live_webm()
    with av.open(io.BytesIO(data)) as source:assert source.duration is None
    response=inbox[3](name='recorded-video.webm',raw=data)
    assert response.status_code==200 and response.json()['status']=='SENT',response.text
    raw,metadata=inbox[4][0]
    assert metadata['mime_type']=='video/mp4' and metadata['filename']=='recorded-video.mp4'
    assert .9<=metadata['duration_seconds']<=1.2
    with av.open(io.BytesIO(raw)) as converted:assert converted.streams.video[0].codec_context.name=='h264'


@pytest.mark.parametrize('width,mode',[(390,'normal'),(1280,'normal'),(390,'oversize'),(390,'hidden'),(390,'permission')])
def test_camera_preview_cancel_and_deliberate_send(inbox,width,mode):
    api=pytest.importorskip('playwright.sync_api');binary=shutil.which('chromium')
    if not binary:pytest.skip('Chromium unavailable')
    client,sessions,ids,post,uploads,sends=inbox
    with api.sync_playwright() as runtime:
        browser=runtime.chromium.launch(executable_path=binary,headless=True,args=['--no-sandbox','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream'])
        try:
            context=browser.new_context(viewport={'width':width,'height':820},permissions=['camera','microphone'])
            page=context.new_page();errors=[];posts=[]
            if mode=='permission':
                page.add_init_script("Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{throw new DOMException('denied','NotAllowedError')}})")
            if mode=='oversize':
                page.add_init_script('''window.MediaRecorder=class {
                  static isTypeSupported(type){return type.includes('webm');}
                  constructor(stream,options){this.state='inactive';this.mimeType=options.mimeType;}
                  start(){this.state='recording';setTimeout(()=>this.ondataavailable({data:new Blob([new Uint8Array(5*1024*1024)])}),10);}
                  stop(){this.state='inactive';setTimeout(()=>this.onstop(),0);}
                };''')
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                request=route.request;url=urlsplit(request.url)
                if url.hostname!='video.test':route.fulfill(status=200,body='');return
                if request.method=='POST':posts.append(url.path)
                response=client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,
                    headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token','idempotency-key'}})
                if url.path.endswith('/media-policy'):
                    value=response.json();value['video_recording']['max_seconds']=1
                    route.fulfill(status=200,json=value);return
                route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k in {'content-type','cache-control'}})
            page.route('**/*',serve)
            page.goto('https://video.test/crm/whatsapp');page.locator(f'.conversation-row[data-conversation-id="{ids[0]}"]').click()
            page.locator('#attachment-button').click();page.locator('#record-video-open').click()
            api.expect(page.locator('#record-video-start')).to_be_enabled()
            page.locator('#record-video-start').click()
            if mode=='permission':
                api.expect(page.locator('#record-video-status')).to_contain_text('Allow camera and microphone')
                assert not sends and not uploads;return
            if mode=='oversize':
                api.expect(page.locator('#record-video-status')).to_contain_text('exceeded the size limit')
                api.expect(page.locator('#record-video-use')).to_be_hidden()
                assert not sends and not uploads;return
            if mode=='hidden':
                api.expect(page.locator('#record-video-stop')).to_be_visible()
                page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'));")
                assert not page.locator('#record-video-dialog').evaluate('el=>el.open')
                assert page.locator('#record-video-live').evaluate('el=>el.srcObject===null')
                assert not sends and not uploads;return
            api.expect(page.locator('#record-video-use')).to_be_visible(timeout=15000)
            assert not sends and not any(path.endswith('/send-media') for path in posts)
            assert page.locator('#record-video-dialog').evaluate('el=>el.scrollWidth<=el.clientWidth')
            page.locator('#record-video-cancel').click();assert not sends
            # A second capture previews locally, then uses the standard attachment path.
            page.locator('#attachment-button').click();page.locator('#record-video-open').click()
            api.expect(page.locator('#record-video-start')).to_be_enabled();page.locator('#record-video-start').click()
            api.expect(page.locator('#record-video-use')).to_be_visible(timeout=15000);page.locator('#record-video-use').click()
            api.expect(page.locator('#attachment-send')).to_be_enabled()
            assert not sends
            page.locator('#attachment-send').click()
            api.expect(page.locator('#attachment-status')).to_contain_text('Attachment sent',timeout=20000)
            assert len(sends)==1 and len(uploads)==1 and not errors,errors
        finally:browser.close()
