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
