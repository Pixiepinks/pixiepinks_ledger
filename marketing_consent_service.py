"""Consent evidence and eligibility only. No campaign or transport implementation."""
import hashlib
import json
import re
import unicodedata
from datetime import datetime,timedelta,timezone
from urllib.parse import urlsplit
from fastapi import HTTPException
from sqlalchemy import or_
from models import (MarketingConsent as Consent,MarketingConsentEvent as Event,MarketingConsentRequest as ConsentRequest,
                    WhatsAppConversation as Conversation,WhatsAppConversationMessage as Message,Customer,Lead,User)
from marketing_identity import phone,register
from marketing_consent_constants import SCOPE,STATUSES,SOURCES,INTERESTS,WORDING,WORDING_VERSION
import conversation_access
from quick_message_service import require_reply_window


def contact_for_conversation(db,conversation):
    normalized=phone(conversation.phone_number)
    key='wa:'+normalized if normalized else f'conversation:{conversation.id}'
    return db.query(Consent).filter_by(identity_key=key).populate_existing().one_or_none()


def valid_identity(db,row):
    if not row.phone_e164 or row.identity_conflict:return False
    matched=False
    for model,field,attribute in [(Customer,'customer_id','phone'),(Conversation,'conversation_id','phone_number'),(Lead,'lead_id','mobile')]:
        identity=getattr(row,field)
        if identity is None:continue
        current=db.query(model).filter(model.id==identity).populate_existing().one_or_none()
        if not current or phone(getattr(current,attribute))!=row.phone_e164:return False
        matched=True
    return matched


def eligibility(db,row):
    row=db.query(Consent).filter_by(id=row.id).populate_existing().one()
    reason=('OPTED_OUT' if row.status=='OPTED_OUT' else 'IDENTITY_MISMATCH' if not valid_identity(db,row) else
            'NO_AFFIRMATIVE_CONSENT' if row.status!='OPTED_IN' else
            'INVALID_SCOPE' if row.scope!=SCOPE else
            'INVALID_SOURCE' if row.source not in SOURCES else
            'WITHDRAWAL_NEWER_THAN_EVIDENCE' if row.opted_out_at and (not row.evidence_at or row.evidence_at<=row.opted_out_at) else
            'MISSING_EVIDENCE' if not (row.affirmative and row.evidence_reference and row.wording and row.wording_version and row.evidence_at and row.consent_at and row.last_verified_at) else
            'REVIEW_REQUIRED' if row.needs_review else None)
    if reason is None and not db.query(Event.id).filter(Event.consent_id==row.id,Event.new_status=='OPTED_IN',Event.scope==SCOPE,
            Event.event_type.in_(['CONSENT_GRANTED','CONSENT_VERIFIED','CONSENT_EVIDENCE_CORRECTED']),
            Event.source==row.source,Event.evidence_reference==row.evidence_reference,Event.evidence_at==row.evidence_at,
            Event.wording_version==row.wording_version,Event.wording==row.wording).first():reason='AUDIT_EVIDENCE_MISSING'
    if reason is None and row.source=='WHATSAPP_EXPLICIT_REPLY':
        request=db.query(ConsentRequest).filter_by(consent_id=row.id,whatsapp_message_id=row.source_description).one_or_none()
        if not request or request.delivery_status=='failed':reason='REQUEST_NOT_VERIFIABLE'
    return {'eligible':reason is None,'consent_status':row.status,'scope':row.scope,'evidence_reference':row.evidence_reference,
            'opt_out':row.status=='OPTED_OUT','reason':reason,'last_verified_at':stamp(row.last_verified_at)}


def get_marketing_eligibility(db,customer_id,channel='whatsapp'):
    customer=db.query(Customer).filter_by(id=customer_id).populate_existing().one_or_none()
    normalized=phone(customer.phone) if customer else None
    row=db.query(Consent).filter_by(phone_e164=normalized).populate_existing().one_or_none() if normalized else None
    if channel!='whatsapp' or not row or row.customer_id!=customer_id:
        return {'eligible':False,'consent_status':'NOT_REQUESTED','scope':SCOPE,'evidence_reference':None,'opt_out':False,'reason':'IDENTITY_NOT_VERIFIED','last_verified_at':None}
    return eligibility(db,row)


def stamp(value):return value.isoformat()+'Z' if value else None


def public(db,row,full=False):
    result={'id':row.id,'customer_id':row.customer_id,'conversation_id':row.conversation_id,'name':row.display_name,
        'phone':row.phone_e164,'status':row.status,'scope':row.scope,'source':row.source,'consent_at':stamp(row.consent_at),
        'updated_at':stamp(row.updated_at),'last_verified_at':stamp(row.last_verified_at),'opted_out_at':stamp(row.opted_out_at),
        'interests':row.interests or [],'needs_review':row.needs_review,'identity_conflict':row.identity_conflict,
        'version':row.version,'eligible':eligibility(db,row)['eligible']}
    request=db.query(ConsentRequest).filter_by(consent_id=row.id).order_by(ConsentRequest.id.desc()).first()
    result['latest_request']={'status':('UNKNOWN' if request.status=='SENDING' and request.created_at<datetime.utcnow()-timedelta(minutes=2) else request.status),'delivery_status':request.delivery_status,'sent_at':stamp(request.sent_at)} if request else None
    result['opt_out_source']=row.opt_out_source
    if full:result.update(evidence_reference=row.evidence_reference,evidence_type=row.evidence_type,wording=row.wording,source_description=row.source_description)
    return result


def event(db,row,previous,kind,key,actor=None,reason=None,occurred=None,evidence=None):
    data=evidence or {}
    db.add(Event(consent_id=row.id,dedup_key=key,event_type=kind,previous_status=previous,new_status=row.status,
        customer_id=row.customer_id,conversation_id=row.conversation_id,phone_e164=row.phone_e164,source=data.get('source',row.source),scope=SCOPE,
        wording_version=data.get('wording_version',row.wording_version),wording=data.get('wording',row.wording),
        evidence_type=data.get('evidence_type',row.evidence_type),evidence_reference=data.get('evidence_reference',row.evidence_reference),
        evidence_at=data.get('evidence_at',row.evidence_at),evidence_text=data.get('evidence_text'),source_description=data.get('source_description',row.source_description),
        actor_id=actor.id if actor else None,actor_name=actor.username if actor else 'system:whatsapp',reason=reason,
        occurred_at=occurred or datetime.utcnow()))


def change(db,row,expected,values):
    changed=db.query(Consent).filter_by(id=row.id,version=expected).update(
        {'updated_by':None,**values,'version':expected+1,'updated_at':datetime.utcnow()},synchronize_session=False)
    if not changed:raise HTTPException(409,'Consent changed. Refresh before trying again.')
    db.refresh(row)


def timestamp(value):
    try:
        date=datetime.fromisoformat(value.replace('Z','+00:00'))
        if date.tzinfo is None:raise ValueError()
        date=date.astimezone(timezone.utc).replace(tzinfo=None)
        if date>datetime.utcnow()+timedelta(minutes=1):raise ValueError()
        return date
    except (ValueError,AttributeError,TypeError):raise HTTPException(422,'Provide an evidence timestamp including its timezone; future evidence is not accepted.') from None


def validate_evidence(body):
    if body.get('source') not in SOURCES or body.get('affirmative') is not True or body.get('scope')!=SCOPE:
        raise HTTPException(422,'Explicit affirmative agreement for PixiePinks WhatsApp promotions is required.')
    for key,maximum in [('source_description',1000),('wording',4000),('wording_version',100),('evidence_reference',500),('evidence_type',30)]:
        value=body.get(key)
        if not isinstance(value,str) or not value.strip() or len(value)>maximum or any(ord(c)<32 and c not in '\n\r\t' for c in value):raise HTTPException(422,'Complete source description, wording, version and evidence reference are required.')
    if not all(term in body['wording'].casefold() for term in ['pixiepinks','whatsapp']):raise HTTPException(422,'Consent wording must identify PixiePinks and WhatsApp.')
    if body['evidence_type'] not in {'EXTERNAL_REFERENCE','CRM_MESSAGE'}:raise HTTPException(422,'Use an external evidence reference or CRM message reference.')
    ref=body['evidence_reference']
    if '://' in ref:
        parsed=urlsplit(ref)
        if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password:raise HTTPException(422,'Evidence links must be HTTPS without embedded credentials.')
    return {key:body[key].strip() for key in ['source','source_description','wording','wording_version','evidence_reference','evidence_type']}|{'evidence_at':timestamp(body.get('evidence_at'))}


def apply_action(db,row,user,body):
    action=body.get('action');key=body.get('key');expected=body.get('version')
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',key) or type(expected) is not int or expected<0:raise HTTPException(422,'Provide the current version and a unique action key.')
    dedup='staff:'+str(user.id)+':'+key
    prior=db.query(Event).filter_by(dedup_key=dedup).one_or_none()
    fingerprint=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    if prior:
        if prior.consent_id!=row.id or prior.action_fingerprint!=fingerprint:raise HTTPException(409,'Action key belongs to another contact.')
        return
    previous=row.status;now=datetime.utcnow();reason=body.get('reason','')
    if not isinstance(reason,str) or len(reason)>500 or any(ord(c)<32 and c not in '\n\r\t' for c in reason):raise HTTPException(422,'Reason must be at most 500 characters.')
    if action=='interests':
        tags=body.get('interests')
        if not isinstance(tags,list) or len(tags)>len(INTERESTS) or any(tag not in INTERESTS for tag in tags):raise HTTPException(422,'Choose existing product interest tags.')
        values={'interests':sorted(set(tags))};kind='INTERESTS_UPDATED'
    elif action=='keep_pending':
        if row.status!='PENDING' or not row.needs_review or not reason.strip():raise HTTPException(409,'An unresolved pending review and reason are required.')
        values={'needs_review':True};kind='RESPONSE_REVIEW_DEFERRED'
    elif action=='opt_out':
        if not reason.strip():raise HTTPException(422,'Record the customer withdrawal evidence or reason.')
        values={'status':'OPTED_OUT','opted_out_at':now,'opt_out_source':'STAFF_VERIFIED_EXTERNAL','opt_out_reason':reason,
                'active_request_id':None,'affirmative':False,'needs_review':False};kind='CONSENT_WITHDRAWN'
    elif action in {'verify','correct','invalidate','reconcile_identity'}:
        if user.role!='ADMIN':raise HTTPException(403,'Only administrators may verify, correct or invalidate marketing consent evidence.')
        if not reason.strip():raise HTTPException(422,'An administrative reason is required.')
        if action=='reconcile_identity':
            if not row.phone_e164:raise HTTPException(422,'There is no normalized phone identity to reconcile.')
            matches={}
            for model,field,attr in [(Customer,'customer_id','phone'),(Conversation,'conversation_id','phone_number'),(Lead,'lead_id','mobile')]:
                matches[field]=[item for item in db.query(model).yield_per(200) if phone(getattr(item,attr))==row.phone_e164]
            if len(matches['customer_id'])>1 or len(matches['conversation_id'])>1 or not any(matches.values()) or (not matches['customer_id'] and not matches['conversation_id'] and len(matches['lead_id'])!=1):
                raise HTTPException(409,'Resolve duplicate or missing canonical CRM identities before reconciliation.')
            values={field:items[0].id if len(items)==1 else None for field,items in matches.items()}
            values.update(identity_conflict=False,status='OPTED_OUT' if previous=='OPTED_OUT' else 'NOT_REQUESTED',affirmative=False,active_request_id=None,needs_review=False)
            kind='CONSENT_INVALIDATED'
        elif action=='invalidate':
            values={'status':'OPTED_OUT' if previous=='OPTED_OUT' else 'NOT_REQUESTED','affirmative':False,'active_request_id':None,'needs_review':False};kind='CONSENT_INVALIDATED'
        else:
            if not valid_identity(db,row):raise HTTPException(422,'Resolve the invalid or ambiguous canonical phone identity first.')
            evidence=validate_evidence(body)
            if row.opted_out_at and (evidence['source']!='CUSTOMER_REQUEST' or evidence['evidence_at']<=row.opted_out_at):raise HTTPException(409,'An opt-out requires fresh customer-initiated consent evidence; imports cannot overwrite it.')
            if evidence['source']=='WHATSAPP_EXPLICIT_REPLY':raise HTTPException(422,'Use a linked active Inbox request for WhatsApp replies, or record independently verified external evidence.')
            values={**evidence,'status':'OPTED_IN','affirmative':True,'consent_at':evidence['evidence_at'],
                    'verified_by':user.id,'last_verified_at':now,'active_request_id':None,'needs_review':False}
            kind='CONSENT_EVIDENCE_CORRECTED' if action=='correct' else 'CONSENT_VERIFIED'
    else:raise HTTPException(422,'Unsupported consent action.')
    change(db,row,expected,{**values,'updated_by':user.id})
    if action in {'invalidate','reconcile_identity'}:
        db.query(ConsentRequest).filter(ConsentRequest.consent_id==row.id,ConsentRequest.status.in_(['PREPARED','SENDING','UNKNOWN'])).update({'status':'INVALIDATED'},synchronize_session=False)
    event(db,row,previous,kind,dedup,user,reason,evidence={'source':'STAFF_VERIFIED_EXTERNAL','evidence_type':'STAFF_NOTE','evidence_reference':'staff-action:'+key,'evidence_at':now} if action=='opt_out' else None)
    for item in db.new:
        if isinstance(item,Event) and item.dedup_key==dedup:item.action_fingerprint=fingerprint


def prepare_request(db,row,conversation,user,language,key,request_format="text"):
    import marketing_consent_buttons as buttons
    if not isinstance(request_format,str) or request_format not in {"text","buttons"}:raise HTTPException(422,"Choose text or buttons for the consent request.")
    conversation_access.require_owned(db,user,conversation.id);require_reply_window(db,conversation)
    if not isinstance(language,str) or language not in WORDING or not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',key):raise HTTPException(422,'Choose a request language and unique key.')
    prior=db.query(ConsentRequest).filter_by(idempotency_key=key).one_or_none()
    if prior:
        if (prior.consent_id,prior.actor_id,prior.conversation_id,prior.language,prior.phone_e164)!=(row.id,user.id,conversation.id,language,row.phone_e164) or buttons.enabled(prior)!=(request_format=='buttons'):raise HTTPException(409,'Request key belongs to another contact, language or actor.')
        if prior.status=='PREPARED' and prior.expires_at<datetime.utcnow():raise HTTPException(409,'This preview expired. Start a new review.')
        return prior
    if not valid_identity(db,row) or row.status!='NOT_REQUESTED':raise HTTPException(409,'Consent is already pending, granted or opted out, or this identity needs review. Do not repeatedly request permission.')
    now=datetime.utcnow()
    if db.query(ConsentRequest.id).filter(ConsentRequest.actor_id==user.id,ConsentRequest.created_at>now-timedelta(hours=1)).count()>=10:
        raise HTTPException(429,'Consent preview limit reached. Try again later.')
    if db.query(ConsentRequest.id).filter(ConsentRequest.consent_id==row.id,ConsentRequest.sent_at>now-timedelta(days=1)).first():
        raise HTTPException(409,'A consent request was already sent in the last 24 hours. Do not repeatedly ask.')
    active=db.query(ConsentRequest).filter(ConsentRequest.consent_id==row.id,or_(ConsentRequest.status.in_(['SENDING','UNKNOWN']), (ConsentRequest.status=='PREPARED') & (ConsentRequest.expires_at>now))).first()
    if active:
        if active.status=='PREPARED' and active.actor_id==user.id and (active.language!=language or buttons.enabled(active)!=(request_format=='buttons')):active.status='CANCELLED'
        else:raise HTTPException(409,'An existing consent request must be reviewed first.')
    # Reserve a version to serialize previews from different staff. No state becomes pending yet.
    change(db,row,row.version,{'updated_by':user.id})
    request=ConsentRequest(consent_id=row.id,conversation_id=conversation.id,phone_e164=row.phone_e164,actor_id=user.id,
        idempotency_key=key,language=language,wording_version=(buttons.VERSION if request_format=='buttons' else WORDING_VERSION)+language,message_text=(buttons.BODY if request_format=='buttons' else WORDING)[language],
        base_version=row.version,expires_at=now+timedelta(minutes=30))
    db.add(request);db.flush()
    if request_format=="buttons":buttons.prepare(db,request,row,user)
    return request


def validate_request_send(db,user,conversation,request_id,key,text):
    request=db.get(ConsentRequest,request_id)
    if not request or (request.conversation_id,request.actor_id,request.idempotency_key,request.message_text)!=(conversation.id,user.id,key,text):raise HTTPException(409,'Consent request does not match its reviewed text, recipient, actor or send key.')
    row=db.get(Consent,request.consent_id)
    if request.status!='PREPARED' or request.expires_at<datetime.utcnow() or row.version!=request.base_version or row.status!='NOT_REQUESTED' or not valid_identity(db,row):raise HTTPException(409,'Consent request is no longer current. Refresh; no message was sent.')
    return request


def start_request(db,request):
    row=db.get(Consent,request.consent_id);change(db,row,request.base_version,{'updated_by':request.actor_id})
    request.base_version=row.version;request.status='SENDING'


def finish_request(db,request,message_id,local_id=None,outcome='UNKNOWN'):
    now=datetime.utcnow();request.status='SENT' if message_id else outcome
    if not message_id:return
    request.whatsapp_message_id=message_id;request.local_message_id=local_id;request.sent_at=now;request.expires_at=now+timedelta(days=7)
    row=db.query(Consent).filter_by(id=request.consent_id).with_for_update().populate_existing().one()
    previous=row.status
    if row.version==request.base_version and row.status=='NOT_REQUESTED':
        change(db,row,row.version,{'status':'PENDING','active_request_id':request.id,'source':'WHATSAPP_EXPLICIT_REPLY',
            'wording':request.message_text,'wording_version':request.wording_version,'updated_by':request.actor_id})
    event(db,row,previous,'CONSENT_REQUESTED','request:'+str(request.id),actor=db.get(User,request.actor_id),reason='Staff explicitly sent the reviewed consent request.',evidence={
        'source':'WHATSAPP_EXPLICIT_REPLY','wording':request.message_text,'wording_version':request.wording_version,
        'evidence_reference':'request:'+str(request.id),'evidence_type':'CRM_REQUEST','evidence_at':now})


def inbound(db,message):
    normalized=phone(message.phone_number)
    if not normalized or message.direction!='inbound' or not message.whatsapp_message_id:return
    row=db.query(Consent).filter_by(phone_e164=normalized).with_for_update().populate_existing().one_or_none()
    if not row:return
    key='wa:'+hashlib.sha256(message.whatsapp_message_id.encode()).hexdigest()
    if db.query(Event.id).filter_by(dedup_key=key).first():return
    normalized_text=unicodedata.normalize('NFKC',message.message_text).translate({ord(c):None for c in '\u200b\u200c\u200d\ufeff'})
    canonical=lambda value:' '.join(unicodedata.normalize('NFKC',value).translate({ord(c):None for c in '\u200b\u200c\u200d\ufeff'}).casefold().split()).rstrip('.!')
    text=canonical(normalized_text)
    stops={'stop','unsubscribe','remove me','do not send promotions','no promotions','stop please','please unsubscribe','please do not send promotions','no, do not send promotions','නවත්වන්න','ප්‍රවර්ධන පණිවිඩ එවන්න එපා','நிறுத்து','விளம்பர செய்திகள் அனுப்ப வேண்டாம்'}
    yes={'yes','yes, i agree','yes i agree','i agree to receive pixiepinks promotions','ඔව්','ஆம்'}
    stops={canonical(value) for value in stops};yes={canonical(value) for value in yes}
    declines={canonical(value) for value in ['no','no thanks','නැහැ','இல்லை']}
    request=db.get(ConsentRequest,row.active_request_id) if row.active_request_id else None
    active=bool(request and row.status=='PENDING' and request.status=='SENT' and request.phone_e164==normalized and request.sent_at and request.sent_at<=message.created_at<=request.expires_at and request.delivery_status!='failed')
    linked=bool(active and request.whatsapp_message_id and message.reply_to_whatsapp_message_id==request.whatsapp_message_id)
    previous=row.status;now=datetime.utcnow();values={};kind=None
    evidence={'source':'CUSTOMER_REQUEST','evidence_type':'CRM_MESSAGE','evidence_reference':'message:'+str(message.id),'evidence_at':message.created_at,'evidence_text':message.message_text}
    if text in stops or (linked and text in declines):
        kind='CONSENT_DECLINED' if linked and previous=='PENDING' and text not in stops else 'CONSENT_WITHDRAWN'
        values={'status':'OPTED_OUT','affirmative':False,'opted_out_at':now,'opt_out_source':'CUSTOMER_REQUEST','opt_out_reason':'Explicit customer withdrawal/decline.',
                'active_request_id':None,'needs_review':False}
    elif linked and text in yes and valid_identity(db,row):
        kind='CONSENT_GRANTED';values={'status':'OPTED_IN','affirmative':True,'source':'WHATSAPP_EXPLICIT_REPLY',
            'source_description':request.whatsapp_message_id,'consent_at':message.created_at,'wording':request.message_text,
            'wording_version':request.wording_version,'evidence_type':'CRM_MESSAGE','evidence_reference':'message:'+str(message.id),
            'evidence_at':message.created_at,'last_verified_at':now,'needs_review':False,'active_request_id':None}
        evidence={**evidence,'source':'WHATSAPP_EXPLICIT_REPLY','wording':request.message_text,'wording_version':request.wording_version}
        request.status='ANSWERED'
    elif linked or (active and text in yes|declines):
        kind='RESPONSE_REVIEW_REQUIRED';values={'needs_review':True}
        code=('MISSING_REQUEST_REFERENCE' if not request.whatsapp_message_id else
              'MISSING_REPLY_CONTEXT' if not message.reply_to_whatsapp_message_id else
              'MISMATCHED_REPLY_CONTEXT' if message.reply_to_whatsapp_message_id!=request.whatsapp_message_id else
              'IDENTITY_CONFLICT' if not valid_identity(db,row) else 'OTHER_VERIFICATION_FAILURE')
        review_reason=json.dumps({'v':1,'code':code,'request_id':request.id,'message_id':message.id},separators=(',',':'))
    if kind:
        change(db,row,row.version,values);event(db,row,previous,kind,key,reason=review_reason if kind=='RESPONSE_REVIEW_REQUIRED' else 'Customer message; no automated reply was sent.',occurred=message.created_at,evidence=evidence)


def delivery(db,whatsapp_id,status):
    request=db.query(ConsentRequest).filter_by(whatsapp_message_id=whatsapp_id).one_or_none()
    if not request:return
    request.delivery_status=status
    if status!='failed':return
    key=f'delivery:{request.id}:failed'
    if db.query(Event.id).filter_by(dedup_key=key).first():return
    row=db.query(Consent).filter_by(id=request.consent_id).with_for_update().populate_existing().one()
    related=row.active_request_id==request.id or (row.source=='WHATSAPP_EXPLICIT_REPLY' and row.source_description==whatsapp_id)
    if not related or row.status=='OPTED_OUT':return
    previous=row.status;values={'needs_review':True}
    if row.status=='OPTED_IN':values.update(status='NOT_REQUESTED',affirmative=False,active_request_id=None)
    change(db,row,row.version,values)
    event(db,row,previous,'CONSENT_INVALIDATED' if previous=='OPTED_IN' else 'REQUEST_DELIVERY_FAILED',key,
          reason='Meta reported that the consent request failed delivery; administrator review is required.')


REVIEW_REASONS={
    'MISSING_REPLY_CONTEXT':'Customer response has no WhatsApp Reply reference.',
    'MISMATCHED_REPLY_CONTEXT':'Customer replied to a different message.',
    'IDENTITY_CONFLICT':'Customer identity requires verification.',
    'MISSING_REQUEST_REFERENCE':'Request or response evidence is incomplete.',
    'OTHER_VERIFICATION_FAILURE':'Another verification condition failed.',
}


def response_review(db,row):
    """Read retained evidence; never infer linkage for historical audit entries."""
    ev=db.query(Event).filter_by(consent_id=row.id,event_type='RESPONSE_REVIEW_REQUIRED').order_by(Event.id.desc()).first()
    if not ev:return None
    metadata={}
    try:
        parsed=json.loads(ev.reason or '')
        if isinstance(parsed,dict) and parsed.get('v')==1:metadata=parsed
    except (ValueError,TypeError):pass
    message=None
    if ev.evidence_reference and re.fullmatch(r'message:\d+',ev.evidence_reference):
        message=db.get(Message,int(ev.evidence_reference.split(':')[1]))
    # Legacy events have no durable request association. Do not guess from the current request.
    request=db.get(ConsentRequest,metadata.get('request_id')) if type(metadata.get('request_id')) is int else None
    if request and request.consent_id!=row.id:request=None
    identity_ok=valid_identity(db,row)
    complete=bool(request and message and request.status=='SENT' and request.whatsapp_message_id
        and request.sent_at and message.whatsapp_message_id and message.direction=='inbound'
        and phone(message.phone_number)==row.phone_e164==request.phone_e164
        and request.sent_at<=message.created_at<=request.expires_at and request.delivery_status!='failed'
        and metadata.get('message_id')==message.id and message.created_at==ev.evidence_at
        and message.message_text==ev.evidence_text and request.message_text==ev.wording
        and request.wording_version==ev.wording_version and row.active_request_id==request.id)
    text=' '.join(unicodedata.normalize('NFKC',message.message_text if message else '').casefold().split()).rstrip('.!')
    affirmative=text in {'yes','yes, i agree','yes i agree','i agree to receive pixiepinks promotions','ඔව්','ஆம்'}
    contradictory=bool(message and request and message.reply_to_whatsapp_message_id and message.reply_to_whatsapp_message_id!=request.whatsapp_message_id)
    allowed=bool(complete and identity_ok and affirmative and not contradictory and row.status=='PENDING' and row.needs_review
        and not row.opted_out_at and row.wording==request.message_text and row.wording_version==request.wording_version)
    code=metadata.get('code')
    return {'event_id':ev.id,'reason_code':code if code in REVIEW_REASONS else None,
        'reason':REVIEW_REASONS.get(code,'Review required — exact reason not recorded.'),
        'identity_verified':identity_ok,'can_approve':allowed,'unresolved':row.status=='PENDING' and row.needs_review,
        'request_id':request.id if request else None,'request_text':request.message_text if request else ev.wording,
        'request_at':stamp(request.sent_at) if request else None,'request_message_id':request.whatsapp_message_id if request else None,
        'response_text':ev.evidence_text,'response_at':stamp(ev.evidence_at),
        'response_message_id':message.whatsapp_message_id if message else None,
        'reply_to_message_id':message.reply_to_whatsapp_message_id if message else None,
        'evidence_reference':ev.evidence_reference}


def review_action(db,row,user,body):
    """Approve only retained evidence, through the original verification policy."""
    if user.role!='ADMIN':raise HTTPException(403,'Only administrators may verify consent responses.')
    # Preserve the original service's action fingerprint and replay semantics.
    key=body.get('key');dedup='staff:'+str(user.id)+':'+str(key)
    fingerprint=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    prior=db.query(Event).filter_by(dedup_key=dedup).one_or_none()
    if prior:
        if prior.consent_id!=row.id or prior.action_fingerprint!=fingerprint:raise HTTPException(409,'Review key belongs to another decision.')
        return
    review=response_review(db,row)
    if not review or type(body.get('event_id')) is not int or body['event_id']!=review['event_id']:raise HTTPException(409,'Refresh the current response evidence.')
    if not review['unresolved']:raise HTTPException(409,'This response no longer needs review.')
    decision=body.get('decision')
    data={'key':key,'version':body.get('version'),'reason':body.get('reason')}
    if decision=='approve':
        if body.get('confirmed') is not True or not review['can_approve']:raise HTTPException(422,'Complete, non-contradictory affirmative evidence and explicit confirmation are required.')
        request=db.get(ConsentRequest,review['request_id'])
        data.update(action='verify',source='CUSTOMER_REQUEST',scope=SCOPE,affirmative=True,
            source_description='Administrator reviewed request:'+str(request.id)+' and '+review['evidence_reference'],
            wording=request.message_text,wording_version=request.wording_version,evidence_type='CRM_MESSAGE',
            evidence_reference=review['evidence_reference'],evidence_at=review['response_at'])
    elif decision=='pending':data['action']='keep_pending'
    elif decision=='decline':
        text=' '.join((review['response_text'] or '').casefold().split()).rstrip('.!')
        if text not in {'no','no thanks','stop','unsubscribe','නැහැ','නවත්වන්න','இல்லை','நிறுத்து'}:raise HTTPException(422,'The retained response does not establish a decline. Record separate withdrawal evidence through the existing workflow.')
        data['action']='opt_out'
    else:raise HTTPException(422,'Choose a supported review decision.')
    apply_action(db,row,user,data)
    for ev in db.new:
        if isinstance(ev,Event) and ev.dedup_key==dedup:
            ev.action_fingerprint=fingerprint
            ev.source_description='Reviewed consent event:'+str(review['event_id'])+'; request:'+str(review['request_id'])
            ev.evidence_reference=review['evidence_reference']
            ev.evidence_type='CRM_MESSAGE'
            ev.evidence_at=timestamp(review['response_at']) if review['response_at'] else None
