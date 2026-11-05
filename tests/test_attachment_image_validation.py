"""Generated image fixtures only; provider transports remain mocked."""
import io
import random
import struct
import zlib
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
import outbound_media_service as service
from test_whatsapp_outbound_media import inbox,jpeg


def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))


def png(width=900,height=895,depth=8,color=2,noise=True,noise_rows=None):
    channels={0:1,2:3,3:1,4:2,6:4}[color];stride=(width*channels*depth+7)//8
    rng=random.Random(7)
    scanlines=b''.join(b'\0'+(rng.randbytes(stride) if noise and (noise_rows is None or row<noise_rows) else bytes(stride)) for row in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,depth,color,0,0,0))+(chunk(b'PLTE',bytes(6)) if color==3 else b'')+chunk(b'IDAT',zlib.compress(scanlines))+chunk(b'IEND',b'')


def test_valid_242mb_png_reaches_mocked_upload_and_send(inbox):
    _,_,_,post,uploads,sends=inbox;raw=png()
    assert 2_400_000<len(raw)<2_450_000
    response=post('photo.png',raw,mime='application/octet-stream')
    assert response.status_code==200,response.text
    assert len(uploads)==len(sends)==1 and uploads[0][0]==raw
    assert uploads[0][1]['mime_type']=='image/png'


@pytest.mark.parametrize('depth,color',[(8,0),(8,2),(8,6),(16,2),(16,6),(1,3)])
def test_supported_png_color_modes_remain_decodable(depth,color):
    raw=png(width=96,height=96,depth=depth,color=color,noise=False)
    assert service.validate_file(raw,'image.png','')['file_size_bytes']==len(raw)


def test_jpeg_under_five_mb_and_configured_limit_enforced(monkeypatch):
    # Legal JPEG COM segment padding, before EOI; still decoded fully by PyAV.
    raw=jpeg();padding=b''
    while len(raw)+len(padding)<4_850_000:
        data=b'x'*60000;padding+=b'\xff\xfe'+struct.pack('>H',len(data)+2)+data
    raw=raw[:-2]+padding+raw[-2:]
    assert 4_194_304<len(raw)<5_000_000
    monkeypatch.setattr(service.settings,'WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES',5_000_000)
    assert service.validate_file(raw,'photo.jpeg','')['mime_type']=='image/jpeg'
    monkeypatch.setattr(service.settings,'WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES',4*1024*1024)
    with pytest.raises(HTTPException) as exc:service.validate_file(raw,'photo.jpeg','')
    assert exc.value.status_code==413 and '4,194,304-byte' in exc.value.detail
    monkeypatch.setattr(service.settings,'WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES',5_000_000)
    with pytest.raises(HTTPException) as exc:service.validate_file(b'x'*5_000_001,'photo.png','')
    assert exc.value.status_code==413 and '5,000,000-byte' in exc.value.detail


def test_png_pixel_bomb_rejected_before_decoder(inbox):
    _,_,_,post,uploads,sends=inbox
    raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',6000,4000,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'))+chunk(b'IEND',b'')
    response=post('large.png',raw)
    assert response.status_code==422 and '20-megapixel' in response.json()['detail']
    assert not uploads and not sends


def test_specific_png_integrity_signature_animation_and_encoding_errors(inbox):
    _,_,_,post,uploads,sends=inbox
    raw=png(width=16,height=16)
    crc=raw[:-1]+bytes([raw[-1]^1])
    cases=[('image.png',crc,'integrity check'),('image.png',b'not-an-image','do not match'),
           ('image.jpg',raw,'do not match'),('image.png',raw[:33]+chunk(b'acTL',struct.pack('>II',1,0))+raw[33:],'Animated PNG'),
           ('image.png',raw[:33]+raw[33:-12],'incomplete'),
           ('image.png',raw[:8]+chunk(b'IHDR',struct.pack('>IIBBBBB',16,16,8,5,0,0,0))+raw[33:],'color/encoding')]
    for index,(name,data,message) in enumerate(cases):
        response=post(name,data,key=f'invalid-image-key-{index:016d}')
        assert response.status_code==422 and message in response.json()['detail'],response.text
        assert 'No message was sent' in response.json()['detail']
    assert not uploads and not sends


@pytest.mark.parametrize('failure,expected',[
    ('timeout','too long'),('memory','memory limit'),('signal','interrupted')])
def test_resource_failures_have_specific_safe_errors(inbox,monkeypatch,failure,expected):
    _,_,_,post,uploads,sends=inbox
    if failure=='timeout':error=service.subprocess.TimeoutExpired('validator',10)
    else:error=service.subprocess.CalledProcessError(-9 if failure=='signal' else 1,'validator',output=b'{"error_code":"validation_memory"}')
    monkeypatch.setattr(service.subprocess,'run',Mock(side_effect=error))
    response=post('image.png',png(width=16,height=16))
    assert response.status_code==422 and expected in response.json()['detail']
    assert not uploads and not sends



def test_jpeg_large_dimensions_rejected_before_decode(inbox):
    _,_,_,post,uploads,sends=inbox
    raw=bytearray(jpeg());position=raw.index(b'\xff\xc0')
    raw[position+5:position+9]=struct.pack('>HH',4000,6000)
    response=post('large.jpg',bytes(raw))
    assert response.status_code==422 and '20-megapixel' in response.json()['detail']
    assert not uploads and not sends



def test_reported_dimensions_and_size_pass_for_staff_and_admin(inbox):
    from models import User
    client,sessions,_,post,uploads,sends=inbox
    raw=png(width=1080,height=1350,noise_rows=746)
    assert 2_400_000<len(raw)<2_450_000
    response=post('same.png',raw,key='same-file-staff-request-123')
    assert response.status_code==200,response.text
    with sessions() as db:
        user=db.query(User).filter_by(username='media-staff').one();user.role='ADMIN';db.commit()
    response=post('same.png',raw,key='same-file-admin-request-123')
    assert response.status_code==200,response.text
    assert len(uploads)==len(sends)==2 and uploads[0][0]==uploads[1][0]==raw
