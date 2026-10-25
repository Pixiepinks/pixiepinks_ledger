"""Exercise compressed Meta responses through real upload/send client code."""
import gzip
import json
import zlib

import pytest

import main
import outbound_media_service as service
from models import WhatsAppConversationMessage, WhatsAppMessageAttachment, WhatsAppMediaSend
from test_whatsapp_outbound_media import inbox, png

UPLOAD=service.upload_media
SEND=service.send_media


def encoded(payload,kind):
    raw=json.dumps(payload).encode()
    if kind=='gzip':return gzip.compress(raw)
    if kind=='deflate':return zlib.compress(raw)
    if kind=='raw-deflate':
        compressor=zlib.compressobj(wbits=-zlib.MAX_WBITS)
        return compressor.compress(raw)+compressor.flush()
    return raw


@pytest.mark.parametrize('encoding',['gzip','deflate','raw-deflate','identity'])
def test_real_compressed_upload_send_and_idempotency(inbox,monkeypatch,encoding):
    client,sessions,ids,post,_,_=inbox
    monkeypatch.setattr(service,'upload_media',UPLOAD);monkeypatch.setattr(service,'send_media',SEND)
    monkeypatch.setattr(service.settings,'META_WHATSAPP_ACCESS_TOKEN','private-test-token')
    monkeypatch.setattr(service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','123456')
    real_client=service.httpx.Client;requests=[]
    def upstream(request):
        requests.append(request)
        assert request.headers['accept-encoding']=='gzip, deflate, identity'
        assert request.url.host=='graph.facebook.com'
        payload={'id':'meta-real-upload'} if request.url.path.endswith('/media') else {'messages':[{'id':'wamid.compressed-real'}]}
        return service.httpx.Response(200,headers={'Content-Encoding':'deflate' if encoding=='raw-deflate' else encoding},
                                      stream=service.httpx.ByteStream(encoded(payload,encoding)))
    monkeypatch.setattr(service.httpx,'Client',lambda **kw:real_client(transport=service.httpx.MockTransport(upstream),**kw))
    response=post();assert response.json()['status']=='SENT',response.text
    assert post().json()['status']=='SENT' and len(requests)==2
    with sessions() as db:
        message=db.query(WhatsAppConversationMessage).one();attachment=db.query(WhatsAppMessageAttachment).one()
        assert message.whatsapp_message_id=='wamid.compressed-real'
        assert attachment.media_id=='meta-real-upload' and attachment.message_id==message.id
        assert db.query(WhatsAppMediaSend).one().status=='SENT'
    body=json.loads(requests[1].content)
    assert body['image']=={'id':'meta-real-upload','caption':'Photo caption'}


@pytest.mark.parametrize('body,encoding',[
    (gzip.compress(b'x'*65537),'gzip'),(zlib.compress(b'x'*65537),'deflate'),
    (gzip.compress(b'{}')[:-2],'gzip'),(b'not gzip','gzip'),
    (gzip.compress(b'{}')+gzip.compress(b'{}'),'gzip'),
    (b'{}','br'),(b'{}','gzip, deflate'),(b'x'*65537,'identity'),
    (b'[1,2]','identity'),(b'<html>private error</html>','identity'),
])
def test_invalid_oversized_or_unsupported_response_is_bounded(body,encoding):
    assert service._decode_meta_json(body,encoding) is None


def test_compressed_rejection_has_only_safe_numeric_diagnostics(inbox,monkeypatch,caplog):
    _,sessions,_,post,_,sends=inbox
    monkeypatch.setattr(service,'upload_media',UPLOAD)
    monkeypatch.setattr(service.settings,'META_WHATSAPP_ACCESS_TOKEN','private-test-token')
    monkeypatch.setattr(service.settings,'META_WHATSAPP_PHONE_NUMBER_ID','123456')
    real_client=service.httpx.Client
    payload={'error':{'code':190,'error_subcode':463,'message':'private-token https://private.example/signed?token=secret','fbtrace_id':'private-trace'}}
    def upstream(request):
        return service.httpx.Response(401,headers={'content-encoding':'gzip'},stream=service.httpx.ByteStream(encoded(payload,'gzip')))
    monkeypatch.setattr(service.httpx,'Client',lambda **kw:real_client(transport=service.httpx.MockTransport(upstream),**kw))
    response=post();assert response.json()['status']=='UPLOAD_FAILED'
    assert response.json()['upload_failure']=={'reason':'http_rejected','http_status':401,'meta_code':190,'meta_error_subcode':463}
    assert 'http_status=401 meta_code=190 meta_subcode=463' in caplog.text
    assert not sends
    for value in ['private-token','private.example','private-trace','signed?token','secret']:
        assert value not in response.text and value not in caplog.text
    with sessions() as db:
        assert db.query(WhatsAppConversationMessage).count()==db.query(WhatsAppMessageAttachment).count()==0
        assert db.query(WhatsAppMediaSend).one().status=='UPLOAD_FAILED'


def test_malformed_diagnostic_fields_are_discarded():
    error=service.MediaUploadFailed(400,{'error':{'code':'private-token','error_subcode':True,'message':'private'}})
    assert error.diagnostic=={'reason':'http_rejected','http_status':400,'meta_code':None,'meta_error_subcode':None}
