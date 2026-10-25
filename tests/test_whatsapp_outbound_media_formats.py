"""Phase 6B format expansion, sharing the independently tested Phase 6A send path."""
import io
import struct
import zipfile
from fractions import Fraction

import av
import pytest

import outbound_media_service as service
from models import WhatsAppMessageAttachment
from test_whatsapp_outbound_media import inbox, png


def mp4(codec='libx264',with_audio=False):
    output=io.BytesIO()
    with av.open(output,'w',format='mp4') as target:
        video=target.add_stream(codec,rate=24);video.width=32;video.height=32;video.pix_fmt='yuv420p';video.thread_count=1
        audio=target.add_stream('aac',rate=48000) if with_audio else None
        for n in range(3):
            frame=av.VideoFrame(32,32,'rgb24');frame.planes[0].update(bytes(frame.planes[0].buffer_size));frame.pts=n
            for packet in video.encode(frame):target.mux(packet)
            if audio:
                sound=av.AudioFrame(format='fltp',layout='mono',samples=1024);sound.sample_rate=48000;sound.pts=n*1024;sound.time_base=Fraction(1,48000)
                sound.planes[0].update(bytes(sound.planes[0].buffer_size))
                for packet in audio.encode(sound):target.mux(packet)
        for packet in video.encode():target.mux(packet)
        if audio:
            for packet in audio.encode():target.mux(packet)
    return output.getvalue()


def office_xml(kind='docx',extra=None):
    output=io.BytesIO()
    word=kind=='docx'
    name='word/document.xml' if word else 'xl/workbook.xml'
    content_type='application/vnd.openxmlformats-officedocument.'+('wordprocessingml.document.main+xml' if word else 'spreadsheetml.sheet.main+xml')
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as package:
        package.writestr('[Content_Types].xml',f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/{name}" ContentType="{content_type}"/></Types>')
        package.writestr(name,'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>' if word else '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>')
        for path,data in (extra or {}).items():package.writestr(path,data)
    return output.getvalue()


def legacy_office(kind='doc',encrypted=False):
    """Minimal structurally valid CFB fixtures with Word FIB / Excel BIFF headers."""
    end,free=0xfffffffe,0xffffffff
    if kind=='doc':
        word=bytearray(4096);struct.pack_into('<HH',word,0,0xa5ec,0x00c1)
        if encrypted:struct.pack_into('<H',word,10,0x100)
        streams=[('WordDocument',bytes(word)),('1Table',bytes(4096))]
    else:
        biff=struct.pack('<HHHHHHII',0x0809,16,0x0600,0x0005,0x0dbb,0x07cc,0x41,0x06)
        if encrypted:biff+=struct.pack('<HHH',0x002f,2,0)
        biff+=struct.pack('<HH',0x000a,0)
        streams=[('Workbook',biff.ljust(4096,b'\0'))]
    header=bytearray(512);header[:8]=b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
    struct.pack_into('<HHHHH',header,24,0x003e,3,0xfffe,9,6)
    struct.pack_into('<IIIIIIIII',header,40,0,1,0,0,4096,end,0,end,0)
    struct.pack_into('<109I',header,76,1,*([free]*108))
    def entry(name,kind,right,child,start,size):
        encoded=(name+'\0').encode('utf-16le')
        return encoded.ljust(64,b'\0')+struct.pack('<HBBIII',len(encoded),kind,1,free,right,child)+bytes(16)+struct.pack('<IQQIQ',0,0,0,start,size)
    directory=entry('Root Entry',5,free,1,end,0)
    fat=[end,0xfffffffd]
    for n,(name,content) in enumerate(streams):
        start=2+n*8
        directory+=entry(name,2,n+2 if n+1<len(streams) else free,free,start,len(content))
        fat.extend(range(start+1,start+8));fat.append(end)
    return bytes(header)+directory.ljust(512,b'\0')+struct.pack('<128I',*fat,*([free]*(128-len(fat))))+b''.join(data for _,data in streams)


@pytest.mark.parametrize('name,content,kind,mime',[
    ('clip.mp4',lambda:mp4(),'video','video/mp4'),
    ('sound-clip.mp4',lambda:mp4(with_audio=True),'video','video/mp4'),
    ('quote.doc',lambda:legacy_office('doc'),'document','application/msword'),
    ('quote.docx',lambda:office_xml('docx'),'document','application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
    ('stock.xls',lambda:legacy_office('xls'),'document','application/vnd.ms-excel'),
    ('stock.xlsx',lambda:office_xml('xlsx'),'document','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
    ('notes.txt',lambda:'Hello customer! ආයුබෝවන්'.encode(),'document','text/plain'),
])
def test_additional_formats_send_and_render(inbox,name,content,kind,mime):
    client,sessions,ids,post,uploads,sends=inbox
    raw=content();response=post(name,raw,caption='Staff caption')
    assert response.status_code==200 and response.json()['status']=='SENT',response.text
    assert uploads[0][1]['mime_type']==mime and sends[0][2]['media_type']==kind
    with sessions() as db:
        row=db.query(WhatsAppMessageAttachment).one();assert row.filename==name and row.file_size_bytes==len(raw)
        if kind=='video':assert 0<row.duration_seconds<=120
    message=client.get(f'/crm/whatsapp/api/conversations/{ids[0]}').json()['messages'][0]
    assert message['attachments'][0]['type']==kind and message['attachments'][0]['caption']=='Staff caption'


@pytest.mark.parametrize('name,content',[
    ('fake.mp4',lambda:png()), ('bad.mp4',lambda:b'\0\0\0\x18ftypisom'+bytes(64)),
    ('unsupported-codec.mp4',lambda:mp4('mpeg4')),
    ('wrong.docx',lambda:office_xml('xlsx')), ('wrong.xlsx',lambda:office_xml('docx')),
    ('macro.docx',lambda:office_xml('docx',{'word/vbaProject.bin':b'x'})),
    ('traversal.xlsx',lambda:office_xml('xlsx',{'../private.txt':b'x'})),
    ('malformed.docx',lambda:b'PK\x03\x04'+bytes(100)),
    ('encrypted.doc',lambda:legacy_office('doc',encrypted=True)),
    ('encrypted.xls',lambda:legacy_office('xls',encrypted=True)),
    ('wrong.doc',lambda:legacy_office('xls')), ('wrong.xls',lambda:legacy_office('doc')),
    ('bad.doc',lambda:b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'+bytes(100)),
    ('binary.txt',lambda:b'Hello\0binary'), ('not-utf8.txt',lambda:b'\xff\xfe\x01\x02'),
    ('zipbomb.docx',lambda:office_xml('docx',{'large.bin':bytes(33*1024*1024)})),
])
def test_malformed_spoofed_encrypted_and_unsafe_documents(inbox,name,content):
    _,_,_,post,uploads,sends=inbox
    assert post(name,content()).status_code==422
    assert not uploads and not sends


def test_video_size_policy_and_document_cap(monkeypatch):
    monkeypatch.setattr(service.settings,'WHATSAPP_OUTBOUND_VIDEO_MAX_BYTES',1024)
    with pytest.raises(Exception) as exc:service.validate_file(mp4()+bytes(1024),'clip.mp4','')
    assert exc.value.status_code==413
    policy=service.policy();assert policy['formats']['.mp4']['max_bytes']==1024
    assert policy['formats']['.docx']['type']=='document'
    assert '.svg' not in policy['formats'] and '.exe' not in policy['formats']


def test_chromium_video_preview_document_download_and_polling(inbox,monkeypatch):
    import json
    import shutil
    from urllib.parse import urlsplit
    import main
    playwright=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,_,ids,_,uploads,sends=inbox
    raw_video=mp4();raw_document=office_xml('docx')
    monkeypatch.setattr(service,'upload_media',lambda raw,data:uploads.append((raw,data)) or f'media-{len(uploads)}')
    monkeypatch.setattr(service,'send_media',lambda phone,media,data:sends.append((phone,media,data)) or ('SENT',f'wamid.unique-{len(sends)}'))
    monkeypatch.setattr(main,'download_whatsapp_attachment',lambda media_id,media_type:(raw_video,'video/mp4') if media_type=='video' else (raw_document,service.FORMATS['.docx'][1]))
    with playwright.sync_playwright() as browser_api:
        browser=browser_api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1000,'height':800});errors=[]
            page.on('pageerror',lambda err:errors.append(str(err)))
            def serve(route):
                request=route.request;parsed=urlsplit(request.url)
                if parsed.hostname!='inbox.test':route.fulfill(status=200,body='');return
                headers={k:v for k,v in request.headers.items() if k in {'content-type','x-csrf-token','idempotency-key','range'}}
                response=client.request(request.method,parsed.path+('?' + parsed.query if parsed.query else ''),headers=headers,content=request.post_data_buffer,follow_redirects=False)
                route.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[0]}')
            page.wait_for_function('!document.getElementById("attachment-button").disabled')
            page.locator('#attachment-button').click();assert page.locator('[data-media-category="video"]').is_enabled()
            page.locator('#attachment-file').set_input_files({'name':'clip.mp4','mimeType':'video/mp4','buffer':raw_video})
            assert page.locator('#attachment-preview video').is_visible()
            page.wait_for_function('document.querySelector("#attachment-preview video").readyState>=1')
            page.locator('#attachment-caption').fill('Staff video');page.locator('#attachment-send').click()
            page.wait_for_function('document.getElementById("attachment-status").textContent.startsWith("Attachment sent")')
            page.locator('#messages .message-attachment video').wait_for()
            page.evaluate('window.retainedVideo=document.querySelector("#messages video")')
            page.wait_for_timeout(4200)
            assert page.evaluate('window.retainedVideo===document.querySelector("#messages video")')
            page.locator('#attachment-remove').click()
            page.locator('#attachment-file').set_input_files({'name':'quote.docx','mimeType':'application/octet-stream','buffer':raw_document})
            page.locator('#attachment-send').click();page.wait_for_function('document.getElementById("attachment-status").textContent.startsWith("Attachment sent")')
            document=page.locator('#messages .message-attachment').filter(has_text='quote.docx')
            with page.expect_download() as download:document.get_by_role('button',name='Download document').click()
            assert download.value.suggested_filename=='quote.docx'
            assert len(sends)==2 and not errors
        finally:browser.close()
