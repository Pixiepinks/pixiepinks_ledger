"""Durable manual Monara generation and delivery. No automatic replies or provider retries."""
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from uuid import uuid4
import hashlib,json,re
from sqlalchemy import func
from sqlalchemy.orm import defer,load_only
from fastapi import HTTPException
from models import User,WhatsAppConversation as C,WhatsAppConversationMessage as M
from monara_models import MonaraPolicy as P,MonaraJob as J,MonaraSend as S,MonaraEvent as E
from settings import settings
import conversation_access as access
from quick_message_service import require_reply_window
import monara_audio
from monara_diagnostics import diagnostic,clean
from monara_styles import PROMPTS,LABELS

DEFAULT={'generation':False,'sending':False,'staff_ids':[],'monthly_characters':50000,'daily_requests_per_staff':20,'monthly_budget':'0','reserve_per_request':'0','price_per_1000_characters':None,'currency':'USD','retention_days':7,'default_style':'friendly','style_prompts':PROMPTS}
DISCLOSURE='AI-generated voice — PixiePinks Monara / AI හඬ පණිවිඩය'

def hashv(v):return hashlib.sha256(json.dumps(v,sort_keys=True,default=str).encode()).hexdigest()
def event(db,user,action,job=None,**detail):db.add(E(staff_id=user.id,job_id=job,action=action,detail=detail))
def policy(db):
    row=db.get(P,1)
    if not row:raise HTTPException(503,'Monara migration is required')
    return {**DEFAULT,**row.data}
def authorize(db,user,*,admin=False):
    from staff_access import allowed
    from outbound_media_service import authorize as inbox_authorize
    inbox_authorize(user)
    if not user.is_active or not allowed(user,'whatsapp'):raise HTTPException(403,'WhatsApp access required')
    if admin:
        if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
    elif user.role!='ADMIN' and user.id not in policy(db)['staff_ids']:raise HTTPException(403,'Monara permission required')
def profile():
    if (settings.MONARA_TTS_MODEL,settings.MONARA_TTS_VOICE_ID,settings.MONARA_TTS_API_VERSION)!=('gemini-3.8-flash-tts','voice_xu8nahptj1p9','v1beta'):raise HTTPException(503,'Exact approved Monara model, custom voice and API version required; no fallback')
def lock_policy(db):
    row=db.query(P).filter_by(id=1).populate_existing().with_for_update().one_or_none()
    if not row:raise HTTPException(503,'Monara migration required')
    return row

def configure(db,user,values):
    authorize(db,user,admin=True)
    if not isinstance(values,dict) or set(values)!={'version','policy'} or type(values['version']) is not int or not isinstance(values['policy'],dict):raise HTTPException(422,'Version and complete policy required')
    p=values['policy']
    if set(p)!=set(DEFAULT) or type(p['generation']) is not bool or type(p['sending']) is not bool or not isinstance(p['default_style'],str) or p['default_style'] not in PROMPTS:raise HTTPException(422,'Invalid policy or unsupported style')
    if not isinstance(p['style_prompts'],dict) or set(p['style_prompts'])!=set(PROMPTS) or any(not isinstance(v,str) or len(v)>1000 or (k!='transcript_only' and not v.strip()) for k,v in p['style_prompts'].items()) or p['style_prompts']['transcript_only']!='':raise HTTPException(422,'Valid bounded non-spoken style presets required')
    if not isinstance(p['staff_ids'],list) or len(p['staff_ids'])>500 or any(type(x) is not int or x<=0 for x in p['staff_ids']):raise HTTPException(422,'Choose valid staff IDs')
    if db.query(User).filter(User.id.in_(p['staff_ids']),User.is_active.is_(True)).count()!=len(set(p['staff_ids'])):raise HTTPException(422,'Staff account unavailable')
    for key,maxi in [('monthly_characters',1000000),('daily_requests_per_staff',100),('retention_days',90)]:
        if type(p[key]) is not int or not 1<=p[key]<=maxi:raise HTTPException(422,'Limit outside safe bounds')
    if not isinstance(p['currency'],str) or not re.fullmatch('[A-Z]{3}',p['currency']):raise HTTPException(422,'Currency code required')
    for key in ('monthly_budget','reserve_per_request','price_per_1000_characters'):
        if p[key] is None and key=='price_per_1000_characters':continue
        try:
            value=Decimal(str(p[key]));assert value.is_finite() and 0<=value<=100000 and value==value.quantize(Decimal('.000001'))
        except (ValueError,InvalidOperation,AssertionError):raise HTTPException(422,'Use nonnegative bounded decimal pricing') from None
    if p['generation'] and (Decimal(str(p['monthly_budget']))<=0 or Decimal(str(p['reserve_per_request']))<=0):raise HTTPException(422,'Explicit positive monthly cost budget and conservative per-request reservation required')
    row=lock_policy(db)
    if p['currency']!=policy(db)['currency'] and db.query(J).filter(J.created_at>=datetime.utcnow().replace(day=1,hour=0,minute=0,second=0,microsecond=0)).first():raise HTTPException(409,'Keep one budget currency throughout the current UTC month')
    if row.version!=values['version']:raise HTTPException(409,'Policy changed; refresh')
    previous={k:v for k,v in policy(db).items() if k!='style_prompts'}
    row.data=p;row.version+=1;event(db,user,'POLICY_CHANGED',version=row.version,previous=previous,current={k:v for k,v in p.items() if k!='style_prompts'},style_digest=hashv(p['style_prompts']));db.flush();return {'version':row.version,'policy':p}

def valid_key(key):
    if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_-]{16,90}',key):raise HTTPException(422,'Idempotency key required')

def create(db,user,cid,values,key):
    authorize(db,user);profile();valid_key(key)
    if not settings.MONARA_TTS_ENABLED:raise HTTPException(403,'Monara generation is disabled')
    if not isinstance(values,dict) or set(values)!={'transcript','language','style'}:raise HTTPException(422,'Transcript, language and style required')
    text=values['transcript']
    if not isinstance(text,str) or not text.strip() or len(text)>min(1000,settings.MONARA_TTS_MAX_CHARACTERS) or re.search('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',text):raise HTTPException(422,'Transcript is empty, too long or contains control characters')
    if not isinstance(values['language'],str) or values['language'] not in ('si','en','ta') or not isinstance(values['style'],str) or values['style'] not in PROMPTS:raise HTTPException(422,'Choose a supported language and style')
    c=access.require_owned(db,user,cid);lock_policy(db);p=policy(db)
    fingerprint=hashv({'staff':user.id,'conversation':cid,'recipient':hashv(c.phone_number),'values':values,'model':settings.MONARA_TTS_MODEL,'voice':settings.MONARA_TTS_VOICE_ID})
    prior=db.query(J).filter_by(request_key=key).one_or_none()
    if prior:
        if prior.fingerprint!=fingerprint:raise HTTPException(409,'Generation key belongs to another request')
        return prior
    if not p['generation'] or not settings.GEMINI_API_KEY:raise HTTPException(403,'Generation policy or provider configuration is unavailable')
    now=datetime.utcnow();month=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0);day=now.replace(hour=0,minute=0,second=0,microsecond=0)
    # Reserve before external calls. Failed/uncertain jobs retain usage reservations.
    if db.query(J).filter(J.staff_id==user.id,J.created_at>=day).count()>=p['daily_requests_per_staff']:raise HTTPException(429,'Staff daily generation limit reached')
    if db.query(J).filter(J.status.in_(['PENDING','GENERATING'])).count()>=max(1,min(4,settings.MONARA_TTS_CONCURRENCY)):raise HTTPException(429,'Monara generation slots are busy')
    stored=db.query(func.coalesce(func.sum(func.coalesce(func.length(J.mp3),0)+func.coalesce(func.length(J.ogg),0)),0)).scalar()
    in_flight=db.query(J).filter(J.status.in_(['PENDING','GENERATING'])).count()
    reserve_bytes=2*min(5000000,settings.MONARA_TTS_MAX_AUDIO_BYTES)
    if stored+(in_flight+1)*reserve_bytes>max(0,min(1000000000,settings.MONARA_TTS_STORAGE_MAX_BYTES)):raise HTTPException(429,'Durable audio storage budget reached; administrator retention review required')
    chars,cost=db.query(func.coalesce(func.sum(J.characters),0),func.coalesce(func.sum(J.reserved_cost),0)).filter(J.created_at>=month).one()
    reserve=Decimal(str(p['reserve_per_request']));budget=Decimal(str(p['monthly_budget']))
    if chars+len(text)>p['monthly_characters'] or reserve<=0 or budget<=0 or Decimal(cost)+reserve>budget:raise HTTPException(429,'Monthly reserved usage budget reached')
    estimate=Decimal(str(p['price_per_1000_characters']))*len(text)/1000 if p['price_per_1000_characters'] is not None else None
    job=J(id=str(uuid4()),request_key=key,fingerprint=fingerprint,staff_id=user.id,conversation_id=cid,recipient_hash=hashv(c.phone_number),status='PENDING',transcript=text,language=values['language'],style=values['style'],style_prompt=p['style_prompts'][values['style']],model=settings.MONARA_TTS_MODEL,voice=settings.MONARA_TTS_VOICE_ID,api_version=settings.MONARA_TTS_API_VERSION,characters=len(text),estimated_cost=estimate,reserved_cost=reserve,usage={'currency':p['currency'],'pricing_basis':'operator-configured input-character estimate; not actual billing'},expires_at=now+timedelta(days=min(p['retention_days'],max(1,settings.MONARA_TTS_RETENTION_DAYS))))
    db.add(job);db.flush();event(db,user,'GENERATION_RESERVED',job.id,characters=len(text));return job

def process(sessions,jid,provider=None):
    with sessions() as db:
        lock_policy(db);job=db.query(J).filter_by(id=jid).with_for_update().one_or_none()
        if not job or job.status!='PENDING':return
        user=db.get(User,job.staff_id)
        try:
            authorize(db,user);profile();c=access.require_owned(db,user,job.conversation_id)
            if not settings.MONARA_TTS_ENABLED or not policy(db)['generation'] or job.recipient_hash!=hashv(c.phone_number):raise HTTPException(403,'Generation no longer authorized')
        except HTTPException:
            job.status='FAILED';job.failure='AUTHORIZATION_CHANGED';event(db,user,'GENERATION_BLOCKED',jid);db.commit();return
        job.status='GENERATING';job.started_at=datetime.utcnow();event(db,user,'GENERATION_STARTED',jid);db.commit()
        stage='provider_execution'
        try:
            output=(provider or monara_audio.generate)(job.transcript,job.model,job.voice,job.api_version,job.style_prompt,job.language)
            # Revalidate even injected provider adapters; media never trusts extension names.
            stage='output_validation'
            from audio_service import validate_voice_audio
            info=validate_voice_audio(output['ogg'])
            if len(output['ogg'])>settings.MONARA_TTS_MAX_AUDIO_BYTES or len(output['mp3'])>settings.MONARA_TTS_MAX_AUDIO_BYTES or info['duration_seconds']>min(90,settings.MONARA_TTS_MAX_AUDIO_SECONDS):raise ValueError()
            stage='storage_commit'
            db.refresh(job)
            if job.status!='GENERATING':return
            job.ogg=output['ogg'];job.mp3=output['mp3'];job.duration=output['duration'];job.sha256=hashlib.sha256(job.ogg).hexdigest();job.status='READY';job.completed_at=datetime.utcnow()
            job.usage={**job.usage,'provider_reported':{k:v for k,v in output.get('usage',{}).items() if k in {'prompt_token_count','candidates_token_count','total_token_count'} and type(v) is int and v>=0}}
            event(db,user,'GENERATION_READY',jid,duration=job.duration);db.commit()
        except Exception as exc:
            detail=clean(getattr(exc,'diagnostic',None)) if hasattr(exc,'diagnostic') else diagnostic(stage,exc)
            db.rollback();job=db.get(J,jid)
            if job.status=='GENERATING':
                job.status='FAILED';job.failure='PROVIDER_OR_ENCODING_FAILED_NO_RETRY';job.usage={**job.usage,'diagnostic':detail};event(db,user,'GENERATION_FAILED',jid,diagnostic=detail);db.commit()

def scoped(db,user,jid,cid=None):
    authorize(db,user);job=db.query(J).options(defer(J.ogg),defer(J.mp3)).filter_by(id=jid).one_or_none()
    if not job:raise HTTPException(404,'Voice not found')
    if job.shared:return job
    c=access.require_visible(db,user,job.conversation_id)
    if not job.shared and (job.staff_id!=user.id or cid!=job.conversation_id or job.recipient_hash!=hashv(c.phone_number)):raise HTTPException(404,'Voice not available in this conversation')
    return job

def public(job):
    return {k:getattr(job,k) for k in ('id','staff_id','conversation_id','status','transcript','language','style','model','voice','characters','duration','shared','active','title','category','failure','created_at','expires_at')}

def audio(db,user,jid,cid,kind):
    job=scoped(db,user,jid,cid)
    if kind not in ('ogg','mp3') or job.status!='READY' or not job.active or job.expires_at<=datetime.utcnow():raise HTTPException(410,'Voice is unavailable or expired')
    raw=getattr(job,kind)
    if not raw or kind=='ogg' and hashlib.sha256(raw).hexdigest()!=job.sha256:raise HTTPException(503,'Voice integrity check failed')
    return raw

def library_save(db,user,jid,cid,values):
    job=scoped(db,user,jid,cid)
    if not isinstance(values,dict) or set(values)!={'title','category','approved'} or values['approved'] is not True:raise HTTPException(422,'Explicit approval to share personalized transcript and audio with authorized staff is required')
    for name,limit in (('title',100),('category',80)):
        if not isinstance(values[name],str) or not 1<=len(values[name].strip())<=limit:raise HTTPException(422,'Title and category required')
    if job.status!='READY' or job.expires_at<=datetime.utcnow():raise HTTPException(409,'Generate and preview a current voice first')
    if job.shared:return public(job)
    job.shared=True;job.title=values['title'].strip();job.category=values['category'].strip();event(db,user,'LIBRARY_APPROVED',jid);db.flush();return public(job)

DISCLOSURES={'si':'මෙය PixiePinks Monara AI මගින් නිර්මාණය කළ හඬ පණිවිඩයකි.','en':'This is an AI-generated voice message from PixiePinks Monara.','ta':'இது PixiePinks Monara உருவாக்கிய AI குரல் செய்தி.'}

def send_status(db,row):
    message=db.get(M,row.message_id) if row.message_id else None
    return {'id':row.id,'job_id':row.job_id,'conversation_id':row.conversation_id,'status':row.status,'whatsapp_message_id':row.whatsapp_message_id,'delivery_status':message.send_status if message else None,'automatic_retry':False,'behavior':'standard_audio_attachment'}

def send(db,user,cid,jid,values,key,upload,deliver):
    authorize(db,user);valid_key(key)
    if not settings.MONARA_TTS_SEND_ENABLED or not policy(db)['sending']:raise HTTPException(403,'Monara sending is disabled')
    if not isinstance(values,dict) or set(values)!={'approved','heard_disclosure','sha256','assignment_version'} or values['approved'] is not True or values['heard_disclosure'] is not True:raise HTTPException(422,'Explicit preview, AI disclosure and send approval required')
    c=access.require_owned(db,user,cid)
    prior=db.query(S).filter_by(request_key=key).one_or_none()
    if prior:
        if (prior.staff_id,prior.conversation_id,prior.job_id)!=(user.id,cid,jid) or prior.fingerprint!=hashv(values):raise HTTPException(409,'Send key belongs to another request')
        return send_status(db,prior)
    job=scoped(db,user,jid,cid)
    raw=audio(db,user,jid,cid,'ogg')
    if not job.transcript.startswith(DISCLOSURES[job.language]):raise HTTPException(422,'Include the displayed AI disclosure in the authored transcript, regenerate and listen before sending')
    if values['sha256']!=job.sha256 or type(values['assignment_version']) is not int or values['assignment_version']!=c.assignment_version:raise HTTPException(409,'Voice or conversation changed; review again')
    require_reply_window(db,c);recipient=c.phone_number;recipient_hash=hashv(recipient);version=c.assignment_version
    existing=db.query(S).filter_by(job_id=jid,conversation_id=cid).one_or_none()
    if existing:raise HTTPException(409,'This audio already has a send intent; inspect its status, never replay an uncertain send')
    row=S(id=str(uuid4()),request_key=key,job_id=jid,fingerprint=hashv(values),staff_id=user.id,conversation_id=cid,recipient_hash=recipient_hash,status='UPLOADING');db.add(row);event(db,user,'SEND_APPROVED',jid,send_id=row.id,library_reuse=job.shared);db.commit()
    try:media=upload(raw)
    except Exception:media=None
    db.expire_all();row=db.get(S,row.id);job=db.get(J,jid)
    if not isinstance(media,str) or not media.strip():row.status='FAILED';event(db,user,'UPLOAD_FAILED',jid);db.commit();return send_status(db,row)
    try:
        authorize(db,user);c=access.require_owned(db,user,cid);require_reply_window(db,c)
        if not settings.MONARA_TTS_SEND_ENABLED or not policy(db)['sending'] or hashv(c.phone_number)!=recipient_hash or c.assignment_version!=version or not job.active or job.status!='READY' or job.sha256!=values['sha256'] or job.expires_at<=datetime.utcnow():raise HTTPException(409,'Authorization changed')
    except HTTPException:row.status='FAILED';event(db,user,'SEND_BLOCKED_AFTER_UPLOAD',jid);db.commit();return send_status(db,row)
    row.media_id=media;row.status='SENDING';row.updated_at=datetime.utcnow();event(db,user,'SEND_DISPATCH_CLAIMED',jid,send_id=row.id);db.commit()
    try:outcome,mid=deliver(recipient,media)
    except Exception:outcome,mid='UNKNOWN',None
    if outcome!='SENT' or not isinstance(mid,str) or not mid.strip():row.status='FAILED' if outcome=='FAILED' else 'UNKNOWN_REVIEW';event(db,user,'SEND_'+row.status,jid,send_id=row.id);db.commit();return send_status(db,row)
    message=M(phone_number=recipient,direction='outbound',message_text='AI-generated Monara voice\n'+job.transcript,whatsapp_message_id=mid,response_kind='monara_voice',send_status='sent',sent_by=user.username,media_id=media,media_mime_type='audio/ogg',media_duration_seconds=job.duration)
    db.add(message);db.flush();row.status='SENT';row.whatsapp_message_id=mid;row.message_id=message.id;row.updated_at=datetime.utcnow();c.last_message_at=datetime.utcnow();event(db,user,'SEND_ACCEPTED',jid,send_id=row.id);db.commit();return send_status(db,row)

def maintain(db,user):
    authorize(db,user,admin=True);now=datetime.utcnow();expired=0;uncertain=0
    lock_policy(db)
    for job in db.query(J).options(defer(J.ogg),defer(J.mp3),defer(J.transcript)).filter(J.expires_at<=now,J.status!='EXPIRED').with_for_update():
        if db.query(S).filter(S.job_id==job.id,S.status.in_(['UPLOADING','SENDING'])).first():continue
        job.ogg=None;job.mp3=None;job.transcript='';job.status='EXPIRED';job.active=False;event(db,user,'RETENTION_EXPIRED',job.id);expired+=1
    for job in db.query(J).filter(J.status.in_(['PENDING','GENERATING']),J.created_at<now-timedelta(minutes=5)).with_for_update():
        job.status='FAILED';job.failure='INTERRUPTED_OUTCOME_REVIEW_NO_RETRY';event(db,user,'INTERRUPTED_GENERATION_REVIEW',job.id);uncertain+=1
    for row in db.query(S).filter(S.status.in_(['UPLOADING','SENDING']),S.updated_at<now-timedelta(minutes=5)).with_for_update():
        row.status='UNKNOWN_REVIEW';event(db,user,'INTERRUPTED_SEND_REVIEW',row.job_id,send_id=row.id);uncertain+=1
    return {'expired':expired,'requires_review':uncertain,'automatic_retries':0}

def summary(db,user):
    authorize(db,user,admin=True);now=datetime.utcnow();month=now.replace(day=1,hour=0,minute=0,second=0,microsecond=0);day=now.replace(hour=0,minute=0,second=0,microsecond=0)
    def counts(since):
        rows=db.query(J).options(load_only(J.completed_at,J.status,J.characters,J.estimated_cost,J.reserved_cost,J.usage,J.staff_id)).filter(J.created_at>=since).all()
        return {'requests':len(rows),'ready':sum(v.completed_at is not None for v in rows),'success_rate':sum(v.completed_at is not None for v in rows)/len(rows) if rows else None,'provider_total_tokens':sum(v.usage.get('provider_reported',{}).get('total_token_count',0) for v in rows),'library_send_reuses':sum(bool(e.detail.get('library_reuse')) for e in db.query(E).filter(E.action=='SEND_APPROVED',E.created_at>=since)),'failed':sum(v.status=='FAILED' for v in rows),'characters':sum(v.characters for v in rows),'estimated_cost':str(sum((v.estimated_cost or Decimal(0) for v in rows),Decimal(0))) if any(v.estimated_cost is not None for v in rows) else None,'reserved_cost':str(sum((v.reserved_cost or Decimal(0) for v in rows),Decimal(0))),'actual_provider_billing':None,'by_staff':{str(uid):sum(v.staff_id==uid for v in rows) for uid in {v.staff_id for v in rows}}}
    return {'daily':counts(day),'monthly':counts(month),'recent_audit':[{'id':v.id,'staff_id':v.staff_id,'action':v.action,'at':v.created_at,'detail':v.detail} for v in db.query(E).order_by(E.id.desc()).limit(100)],'cost_notice':'Estimates/reservations are operator-configured, not actual Gemini billing. Provider token metadata is retained on each job.'}


def playback(db,user,cid,message_id):
    from outbound_media_service import authorize as inbox_authorize
    inbox_authorize(user);access.require_visible(db,user,cid)
    attempt=db.query(S).filter_by(conversation_id=cid,message_id=message_id,status='SENT').one_or_none()
    if not attempt:raise HTTPException(404,'Sent voice not found')
    job=db.get(J,attempt.job_id)
    if not job or not job.ogg or job.expires_at<=datetime.utcnow():raise HTTPException(410,'Voice retention has expired')
    if hashlib.sha256(job.ogg).hexdigest()!=job.sha256:raise HTTPException(503,'Voice integrity check failed')
    return job.ogg
