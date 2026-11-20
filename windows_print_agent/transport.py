"""Only authenticated, bounded outbound TLS to the paired CRM origin."""
from urllib.parse import urlsplit
from uuid import UUID
import hashlib
import httpx
from .protocol import MAX_PDF,VERSION

class AgentError(Exception):pass

class Transport:
    def __init__(self,server,token='',http=None):
        parts=urlsplit(server)
        if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in {'','/'} or parts.port not in {None,443}:raise AgentError('HTTPS CRM origin required')
        self.server='https://'+parts.netloc;self.token=token
        self.http=http or httpx.Client(timeout=httpx.Timeout(20,connect=5),follow_redirects=False,trust_env=False)
    def request(self,method,path,body=None):
        # Paths are constructed by this client, never supplied in remote jobs.
        response=self.http.request(method,self.server+'/api/print-agent/'+path,headers={'Authorization':'Bearer '+self.token} if self.token else {},json=body)
        response.raise_for_status()
        if len(response.content)>256000:raise AgentError('Oversized server response')
        return response.json()
    def pair(self,code):
        value=self.request('POST','pair',{'code':code})
        if value.get('protocol')!=VERSION:raise AgentError('Unsupported agent protocol')
        self.token=value['token'];return self.token
    def config(self):return self.request('GET','config')
    def heartbeat(self,value):return self.request('POST','heartbeat',value)
    def claim(self):return self.request('POST','claim',{})['job']
    def permit(self,job):
        value=self.request('POST','jobs/'+str(UUID(job['id']))+'/permit',{'claim':job['claim']})
        if value!={'authorized':True,'valid_seconds':5}:raise AgentError('Invalid dispatch permit')
    def progress(self,job):
        if self.request('POST','jobs/'+str(UUID(job['id']))+'/progress',{'claim':job['claim']})!={'continue':True}:raise AgentError('Existing dispatch authorization unavailable')
    def result(self,jid,claim,status,spool_id):return self.request('POST','jobs/'+str(UUID(jid))+'/result',{'claim':claim,'status':status,'spool_id':spool_id})
    def pdf(self,job):
        content=bytearray();url=self.server+'/api/print-agent/jobs/'+str(UUID(job['id']))+'/pdf'
        with self.http.stream('GET',url,headers={'Authorization':'Bearer '+self.token,'X-Job-Claim':job['claim']}) as response:
            response.raise_for_status()
            if response.headers.get('content-type','').split(';')[0]!='application/pdf':raise AgentError('PDF response required')
            for chunk in response.iter_bytes(65536):
                content.extend(chunk)
                if len(content)>MAX_PDF:raise AgentError('PDF exceeds limit')
        if len(content)!=job['size'] or hashlib.sha256(content).hexdigest()!=job['sha256'] or not content.startswith(b'%PDF-'):raise AgentError('PDF identity mismatch')
        return bytes(content)

class EncryptedTransport(Transport):
    """Receipt-only application encryption; no reliance on Railway proxy claims."""
    def __init__(self,server,pin,token='',http=None):
        super().__init__(server,token,http)
        if not isinstance(pin,str) or len(pin)!=64:raise AgentError('Copy the public key from authenticated CRM Admin')
        self.pin=pin
    def rpc(self,method,body=None):
        import time
        from . import envelope as wire
        packet,ctx=wire.seal_request(self.pin,{'timestamp':int(time.time()),'origin':self.server,'method':method,'credential':self.token,'body':body or {}})
        content=bytearray()
        with self.http.stream('POST',self.server+'/api/print-connector/envelope',json=packet) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(65536):
                content.extend(chunk)
                if len(content)>wire.MAX_RESPONSE:raise AgentError('Encrypted response too large')
        import json
        try:value=wire.open_response(ctx,json.loads(content))
        except Exception:raise AgentError('Server key/encrypted response invalid; do not print') from None
        if value.get('status')!=200:
            rejected=httpx.Response(value.get('status',503),request=httpx.Request('POST',self.server+'/api/print-connector/envelope'))
            rejected.raise_for_status()
        return value['value']
    def pair(self,code):
        value=self.rpc('pair',{'code':code})
        if value.get('protocol')!=VERSION:raise AgentError('Unsupported agent protocol')
        self.token=value['token'];return self.token
    def config(self):return self.rpc('config')
    def heartbeat(self,value):return self.rpc('heartbeat',value)
    def claim(self):return self.rpc('claim')['job']
    def permit(self,job):
        if self.rpc('permit',{'id':str(UUID(job['id'])),'claim':job['claim']})!={'authorized':True,'valid_seconds':5}:raise AgentError('Invalid dispatch permit')
    def progress(self,job):
        if self.rpc('progress',{'id':str(UUID(job['id'])),'claim':job['claim']})!={'continue':True}:raise AgentError('Dispatch authorization unavailable')
    def result(self,jid,claim,status,spool_id):return self.rpc('result',{'id':str(UUID(jid)),'claim':claim,'status':status,'spool_id':spool_id})
    def pdf(self,job):
        import base64
        from . import envelope as wire
        value=self.rpc('pdf',{'id':str(UUID(job['id'])),'claim':job['claim']})
        try:raw=base64.b64decode(value['pdf'],validate=True)
        except Exception:raise AgentError('Invalid receipt payload') from None
        if len(raw)>wire.MAX_RECEIPT or len(raw)!=job['size'] or hashlib.sha256(raw).hexdigest()!=job['sha256'] or not raw.startswith(b'%PDF-'):raise AgentError('PDF identity mismatch')
        return raw
