"""Persisted, recipient-bound consent buttons. No campaign or automatic transport."""
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime
from fastapi import HTTPException
from models import MarketingConsent as Consent,MarketingConsentRequest as Request,MarketingConsentEvent as Event
from marketing_identity import phone

VERSION='pixiepinks-wa-marketing-v3-buttons-'
LABELS={'yes':'Yes, I agree','no':'No, thanks'}
BODY={
    'si':'''🌸 PixiePinks WhatsApp Updates

PixiePinks වෙතින් අලුත්ම Kids Jeeps, බයිසිකල්, සෙල්ලම් බඩු, Gift Packs සහ විශේෂ දීමනා පිළිබඳ WhatsApp ප්‍රවර්ධන පණිවිඩ ලබාගැනීමට ඔබ කැමතිද?

කැමති නම් පහත “Yes, I agree” බොත්තම තෝරන්න. අකැමැති නම් “No, thanks” තෝරන්න.

ඔබට ඕනෑම අවස්ථාවක STOP ලෙස පිළිතුරු දී ප්‍රවර්ධන පණිවිඩ නතර කළ හැකියි. සාමාන්‍ය පාරිභෝගික සේවාව සඳහා මෙම අනුමැතිය අවශ්‍ය නොවේ.

ස්තූතියි!
PixiePinks 🌸''',
    'en':'''🌸 PixiePinks WhatsApp Updates

Would you like to receive WhatsApp promotional messages from PixiePinks about new Kids Jeeps, bicycles, toys, Gift Packs and special offers?

Tap “Yes, I agree” to agree, or “No, thanks” to decline.

Reply STOP anytime to stop promotional messages. Ordinary customer service does not require marketing permission.

Thank you!
PixiePinks 🌸''',
    'ta':'''🌸 PixiePinks WhatsApp Updates

PixiePinks வழங்கும் Kids Jeeps, சைக்கிள்கள், பொம்மைகள், Gift Packs மற்றும் சிறப்புச் சலுகைகள் பற்றிய WhatsApp விளம்பரச் செய்திகளைப் பெற விரும்புகிறீர்களா?

சம்மதித்தால் “Yes, I agree” பொத்தானைத் தட்டவும். வேண்டாம் என்றால் “No, thanks” என்பதைத் தேர்ந்தெடுக்கவும்.

எப்போது வேண்டுமானாலும் STOP எனப் பதிலளித்து விளம்பரச் செய்திகளை நிறுத்தலாம். வழக்கமான வாடிக்கையாளர் சேவைக்கு விளம்பர அனுமதி தேவையில்லை.

நன்றி!
PixiePinks 🌸''',
}


def enabled(request):return request.wording_version.startswith(VERSION)


def prepare(db,request,row,user):
    token=secrets.token_urlsafe(24)
    buttons=[{'id':f'ppmc:{request.id}:{token}:{choice}','title':title} for choice,title in LABELS.items()]
    db.add(Event(consent_id=row.id,dedup_key='request-buttons:'+str(request.id),event_type='CONSENT_BUTTONS_PREPARED',
        previous_status=row.status,new_status=row.status,phone_e164=row.phone_e164,customer_id=row.customer_id,
        conversation_id=row.conversation_id,scope=row.scope,actor_id=user.id,actor_name=user.username,
        reason=json.dumps({'v':1,'buttons':buttons},separators=(',',':')),occurred_at=datetime.utcnow(),
        wording=request.message_text,wording_version=request.wording_version,evidence_type='CRM_REQUEST',
        evidence_reference='request:'+str(request.id)))


def configuration(db,request):
    if not enabled(request):return None
    ev=db.query(Event).filter_by(consent_id=request.consent_id,dedup_key='request-buttons:'+str(request.id)).one_or_none()
    try:
        data=json.loads(ev.reason) if ev else None
        buttons=data['buttons'] if data and data.get('v')==1 else None
        if not isinstance(buttons,list) or len(buttons)!=2:raise ValueError()
        for button,choice in zip(buttons,LABELS):
            if button['title']!=LABELS[choice] or not re.fullmatch(r'ppmc:'+str(request.id)+r':[A-Za-z0-9_-]{32}:'+choice,button['id']):raise ValueError()
        if ev.wording!=request.message_text or ev.wording_version!=request.wording_version:raise ValueError()
        return buttons
    except (TypeError,KeyError,ValueError,AttributeError):raise HTTPException(409,'Consent button evidence is unavailable. Refresh; no message was sent.') from None


def inbound(db,message,button_id):
    import marketing_consent_service as service
    normalized=phone(message.phone_number)
    if not normalized or not message.whatsapp_message_id:return
    row=db.query(Consent).filter_by(phone_e164=normalized).with_for_update().populate_existing().one_or_none()
    if not row:return
    key='wa:'+hashlib.sha256(message.whatsapp_message_id.encode()).hexdigest()
    if db.query(Event.id).filter_by(dedup_key=key).first():return
    match=re.fullmatch(r'ppmc:([1-9][0-9]{0,10}):[A-Za-z0-9_-]{32}:(yes|no)',button_id)
    request=db.get(Request,int(match[1])) if match else None
    choice=None;code='INVALID_BUTTON_REFERENCE'
    if request and request.consent_id==row.id and request.phone_e164==normalized:
        try:buttons=configuration(db,request)
        except HTTPException:buttons=None
        for button in buttons or []:
            if hmac.compare_digest(button['id'],button_id) and button['title']==message.message_text:choice=match[2]
    if choice and request.status=='SENDING':
        # A fast callback may beat the send transaction's provider-ID commit.
        # Roll back receipt registration so Meta can retry the same delivery.
        raise HTTPException(503,'Consent request acceptance is still being recorded.')
    valid=bool(choice and request.status in {'SENT','ANSWERED'} and request.whatsapp_message_id
        # Meta message timestamps have second precision; matching provider context proves causality.
        and request.sent_at and request.sent_at.replace(microsecond=0)<=message.created_at)
    linked=bool(valid and message.reply_to_whatsapp_message_id==request.whatsapp_message_id)
    active=bool(valid and row.active_request_id==request.id and row.status=='PENDING' and request.status=='SENT'
        and message.created_at<=request.expires_at and request.delivery_status!='failed')
    previous=row.status;now=datetime.utcnow();kind='CONSENT_BUTTON_REJECTED';values={}
    evidence={'source':'CUSTOMER_REQUEST','evidence_type':'CRM_MESSAGE','evidence_reference':'message:'+str(message.id),
        'evidence_at':message.created_at,'evidence_text':message.message_text}
    if linked and choice=='no':
        kind='CONSENT_DECLINED' if row.status=='PENDING' else 'CONSENT_WITHDRAWN'
        values={'status':'OPTED_OUT','affirmative':False,'opted_out_at':now,'opt_out_source':'CUSTOMER_REQUEST',
            'opt_out_reason':'Customer selected the retained No, thanks consent button.','active_request_id':None,'needs_review':False}
        code='EXPLICIT_BUTTON_DECLINE'
    elif linked and choice=='yes' and active and service.valid_identity(db,row) and not row.opted_out_at:
        kind='CONSENT_GRANTED';code='EXPLICIT_BUTTON_AGREEMENT'
        values={'status':'OPTED_IN','affirmative':True,'source':'WHATSAPP_EXPLICIT_REPLY','source_description':request.whatsapp_message_id,
            'consent_at':message.created_at,'wording':request.message_text,'wording_version':request.wording_version,
            'evidence_type':'CRM_MESSAGE','evidence_reference':'message:'+str(message.id),'evidence_at':message.created_at,
            'last_verified_at':now,'needs_review':False,'active_request_id':None}
        request.status='ANSWERED'
        evidence.update(source='WHATSAPP_EXPLICIT_REPLY',wording=request.message_text,wording_version=request.wording_version)
    elif active and choice:
        kind='RESPONSE_REVIEW_REQUIRED';values={'needs_review':True}
        code=('MISSING_REPLY_CONTEXT' if not message.reply_to_whatsapp_message_id else
              'MISMATCHED_REPLY_CONTEXT' if not linked else
              'IDENTITY_CONFLICT' if not service.valid_identity(db,row) else 'OTHER_VERIFICATION_FAILURE')
    elif valid:code='INACTIVE_OR_EXPIRED_REQUEST'
    if values:service.change(db,row,row.version,values)
    reason={'v':1,'code':code,'request_id':request.id if request and request.consent_id==row.id else None,'message_id':message.id,'button_id':button_id[:128]}
    service.event(db,row,previous,kind,key,reason=json.dumps(reason,separators=(',',':')),occurred=message.created_at,evidence=evidence)
