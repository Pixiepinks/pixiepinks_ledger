"""One attempt per durable action, even when delivery, printing or ACK is uncertain."""
import hashlib,json,time
from uuid import UUID
import httpx
from .protocol import *
from .transport import AgentError
from .diagnostics import call, step, traced, failure

class Engine:
    def __init__(self,transport,backend,store,attestation,device_id):
        self.remote=transport;self.backend=backend;self.store=store;self.attestation=attestation;self.device_id=device_id
    def observe(self,event,**fields):
        try:
            monitor=getattr(self,'monitor',None)
            if monitor:monitor(event,**fields)
        except Exception:pass  # Diagnostics never affect dispatch or durability.
    @staticmethod
    def names(config):
        if config['config'].get('receipt_only'):return [RECEIPT_QUEUE]
        return list(dict.fromkeys([RECEIPT_QUEUE,LABEL_QUEUE]+[v for v in [config['config']['drawer_queue'],config['config']['a4_queue']] if v]))
    def verified(self,config):
        names=self.names(config);saved=self.attestation
        normal=saved.get('normal_drawers_none') is True and saved.get('media_and_cutter_tested') is True
        try:
            base=normal and saved.get('device_id')==self.device_id and all(saved.get('fingerprints',{}).get(name)==self.backend.fingerprint(name) for name in names)
            base=base and self.backend.shares_port([RECEIPT_QUEUE] if config['config'].get('receipt_only') else [RECEIPT_QUEUE,LABEL_QUEUE]+([DRAWER_QUEUE] if config['config']['drawer_queue'] else []))
        except Exception as exc:failure('attestation_check',exc);base=False
        try:drawer=base and not config['config'].get('receipt_only') and config['config']['drawer_verified'] and saved.get('drawer_queue_isolated') is True and saved.get('drawer_driver_tested') is True and saved.get('drawer_no_replay_tested') is True and self.backend.drawer_safe(DRAWER_QUEUE) and config['config']['drawer_queue']==DRAWER_QUEUE
        except Exception as exc:failure('attestation_check',exc);drawer=False
        return bool(base),bool(drawer)
    def flush_results(self):
        for jid,claim,status,spool in call('journal_pending',self.store.pending,self.device_id):
            try:call('outcome_ack',self.remote.result,jid,claim,status,spool);call('journal_ack',self.store.acked,jid);self.observe('JOB',job_id=jid,job_status='ACKNOWLEDGED')
            except httpx.HTTPStatusError as error:
                if error.response.status_code==409:call('journal_ack',self.store.acked,jid)  # Operator already resolved; no resend.
                else:raise
    @traced('poll_cycle')
    def cycle(self):
        self.flush_results();config=call('configuration_fetch',self.remote.config)
        if config['id']!=self.device_id or config['status']!='ACTIVE':raise AgentError('Device authorization unavailable')
        validator=getattr(self,'validate_runtime_config',None)
        if validator:validator(config)
        verified,drawer=self.verified(config);queues=call('printer_status',self.backend.status,self.names(config));empty=all(q['jobs']==0 and q['state']=='READY' for q in queues)
        ack=config['mode_request'] if verified and empty else None
        config=call('heartbeat',self.remote.heartbeat,{'version':config['version'],'queues':queues,'queues_empty':empty,'local_verified':verified,'drawer_verified':drawer,'mode_ack':ack})
        if validator:validator(config)
        self.observe('CONNECTED')
        self.observe('READINESS',media=config['media'],printer_ready=bool(verified and empty and config['media'] in MODES and not config['config'].get('printing_paused')))
        if not verified or not empty or config['media'] not in MODES or config['config'].get('printing_paused'):return
        job=call('job_claim',self.remote.claim)
        if not job:return
        self.observe('JOB',job_id=job.get('id'),job_status='DELIVERED')
        try:self.execute(job,config,drawer)
        finally:self.flush_results()
    @traced('job_execution')
    def execute(self,job,config,drawer):
        if config['config'].get('receipt_only') and (job.get('kind') not in {'RECEIPT','TEST'} or job.get('queue')!=RECEIPT_QUEUE or job.get('media')!='RECEIPT' or config['config'].get('receipt_profile')!='80'):raise AgentError('Receipt-only connector cannot dispatch barcode/drawer/other paper')
        str(UUID(job['id']))
        identity=call('journal_identity',self.store.identity,job);prior=call('journal_read',self.store.get,job['id'])
        if prior:
            if prior[0]!=identity:raise AgentError('Existing action identity changed; manual review required')
            return  # Delivered twice or restarted: never dispatch an existing identity again.
        if type(job['size']) is not int or not 0<=job['size']<=MAX_PDF:raise AgentError('Invalid artifact size')
        if job['device_id']!=self.device_id or job['version']!=config['version'] or job['protocol']!=VERSION or job['media']!=config['media'] or job['size']>MAX_PDF:raise AgentError('Invalid job binding')
        if job['kind']=='DRAWER':expected=DRAWER_QUEUE
        elif job['kind']=='LABEL':expected=LABEL_QUEUE
        elif job['kind'] in {'RECEIPT','TEST'}:expected=config['config']['a4_queue'] if job['media']=='A4' else RECEIPT_QUEUE
        else:raise AgentError('Unknown job kind')
        if job['kind'] in {'RECEIPT','TEST'} and job['media'] not in {'RECEIPT','A4'}:raise AgentError('Receipt cannot use label media')
        if job['kind']=='DRAWER' and (job['size']!=0 or job['sha256']!=hashlib.sha256(b'').hexdigest()):raise AgentError('Drawer actions cannot contain remote data')
        if job['queue']!=expected or (job['kind']=='LABEL' and job['media']!='LABEL') or (job['kind']=='DRAWER' and (not drawer or job['media']!='RECEIPT')):raise AgentError('Forbidden queue/control combination')
        if job['kind']=='DRAWER':
            # Fixed controlled slip through the administrator-isolated vendor driver queue.
            # The vendor driver performs its tested cashbox action; no electrical bytes/pulses here.
            from .slip import drawer_slip
            raw=drawer_slip(job['id'])
        else:raw=call('pdf_fetch',self.remote.pdf,job)
        call('pdf_validation',self.backend.validate_pdf,raw,job['media'])
        if config['config'].get('receipt_only'):
            from .windows import WindowsPrinters
            call('receipt80_validation',WindowsPrinters.validate_receipt80,raw)
        call('journal_intent',self.store.intent,job)
        self.observe('JOB',job_id=job['id'],job_status='INTENT')  # fsync before obtaining the single-use dispatch permit or touching a driver.
        try:
            with step('dispatch_lock'),self.backend.exclusive():
                if any(q['jobs'] or q['state']!='READY' for q in call('printer_status',self.backend.status,self.names(config))):
                    call('journal_failed',self.store.finish,job['id'],'FAILED');return
                valid,drawer_valid=self.verified(config)
                if not valid or (job['kind']=='DRAWER' and not drawer_valid):call('journal_failed',self.store.finish,job['id'],'FAILED');return
                started=time.monotonic();call('permit_response',self.remote.permit,job)
                with step('permit_deadline'):
                    if time.monotonic()-started>5:raise AgentError('Dispatch permit too old')
                last_progress=[0.0]
                def progress():
                    if time.monotonic()-last_progress[0]<15:return
                    call('progress_rpc',self.remote.progress,job);call('progress_heartbeat',self.remote.heartbeat,{'version':config['version'],'queues':call('printer_status',self.backend.status,self.names(config)),'queues_empty':False,'local_verified':self.verified(config)[0],'drawer_verified':self.verified(config)[1],'mode_ack':None})
                    last_progress[0]=time.monotonic()
                spool=call('driver_submission',self.backend.submit,job['queue'],raw,job['id'],job['media'],self.attestation['fingerprints'][job['queue']],progress)
                call('journal_submitted',self.store.finish,job['id'],'SUBMITTED_TO_PRINTER',spool)
                self.observe('JOB',job_id=job['id'],job_status='SUBMITTED_TO_PRINTER')
        except Exception:
            call('journal_unknown',self.store.finish,job['id'],'UNKNOWN')
            self.observe('JOB',job_id=job['id'],job_status='UNKNOWN')  # Do not infer whether a physical action occurred.
            raise
