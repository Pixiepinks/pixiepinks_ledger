"""Pinned X25519/HKDF/AES-GCM application tunnel over certificate-verified HTTPS.
Protects the otherwise untrusted HTTP hop after Railway TLS termination. No proxy
header establishes identity; bearer/pairing credentials remain inside encryption.
"""
import base64,json,secrets
from uuid import UUID,uuid4
from dataclasses import dataclass,field
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey,X25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION=1
MAX_REQUEST=65536
MAX_RECEIPT=8*1024*1024
MAX_RESPONSE=16*1024*1024


def encode(raw):return base64.b64encode(raw).decode('ascii')
def decode(value):
    if not isinstance(value,str):raise ValueError('Invalid encrypted envelope')
    return base64.b64decode(value,validate=True)


def private_key(value):
    if not isinstance(value,str) or len(value)!=64:raise ValueError('32-byte connector private key required')
    return X25519PrivateKey.from_private_bytes(bytes.fromhex(value))


def public_hex(value):return private_key(value).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()


@dataclass
class Context:
    request_id:str
    request_key:bytes=field(repr=False)
    response_key:bytes=field(repr=False)
    @property
    def aad(self):return ('PixiePinks-Connector-1:'+self.request_id).encode()


def context(shared,request_id):
    request_id=str(UUID(request_id))
    keys=HKDF(algorithm=hashes.SHA256(),length=64,salt=request_id.encode(),info=b'PixiePinks receipt tunnel v1').derive(shared)
    return Context(request_id,keys[:32],keys[32:])


def seal_request(pin,message):
    if not isinstance(pin,str) or len(pin)!=64:raise ValueError('Pinned connector public key required')
    ephemeral=X25519PrivateKey.generate();pub=X25519PublicKey.from_public_bytes(bytes.fromhex(pin))
    ctx=context(ephemeral.exchange(pub),str(uuid4()));nonce=secrets.token_bytes(12)
    raw=json.dumps(message,separators=(',',':')).encode()
    if len(raw)>MAX_REQUEST//2:raise ValueError('Request exceeds connector limit')
    return {'version':VERSION,'request_id':ctx.request_id,'ephemeral_key':encode(ephemeral.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)),'nonce':encode(nonce),'ciphertext':encode(AESGCM(ctx.request_key).encrypt(nonce,raw,ctx.aad))},ctx


def open_request(secret,packet):
    if not isinstance(packet,dict) or set(packet)!={'version','request_id','ephemeral_key','nonce','ciphertext'} or type(packet['version']) is not int or packet['version']!=VERSION:raise ValueError('Invalid envelope')
    shared=private_key(secret).exchange(X25519PublicKey.from_public_bytes(decode(packet['ephemeral_key'])))
    ctx=context(shared,packet['request_id'])
    raw=AESGCM(ctx.request_key).decrypt(decode(packet['nonce']),decode(packet['ciphertext']),ctx.aad)
    if len(raw)>MAX_REQUEST//2:raise ValueError('Oversized decrypted request')
    return json.loads(raw),ctx


def seal_response(ctx,value):
    raw=json.dumps(value,separators=(',',':')).encode()
    if len(raw)>MAX_RESPONSE*3//4:raise ValueError('Response exceeds connector limit')
    nonce=secrets.token_bytes(12)
    return {'request_id':ctx.request_id,'nonce':encode(nonce),'ciphertext':encode(AESGCM(ctx.response_key).encrypt(nonce,raw,ctx.aad))}


def open_response(ctx,packet):
    if not isinstance(packet,dict) or set(packet)!={'request_id','nonce','ciphertext'} or packet['request_id']!=ctx.request_id:raise ValueError('Response binding mismatch')
    raw=AESGCM(ctx.response_key).decrypt(decode(packet['nonce']),decode(packet['ciphertext']),ctx.aad)
    return json.loads(raw)
