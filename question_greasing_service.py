"""Pinned Saved Voice explanation followed by an explicit service decision."""
from decimal import Decimal
from datetime import datetime
import re
import hashlib
from fastapi import HTTPException
from sqlalchemy import select
from models import SavedVoice,CustomerQuestionProductSend as Item,WhatsAppConversationMessage as Message
from audio_service import NORMALIZATION_VERSION,VoiceAudioError,ffmpeg_encoder_enabled,normalize_voice_audio
import saved_voice_service as library
from whatsapp_service import upload_whatsapp_audio,send_whatsapp_audio

DEFAULT_QUESTION='ඔබගේ බයිසිකලයට මූලික ග්‍රීස් කිරීමේ සේවාව අවශ්‍යද?'
DEFAULT_YES='ඔව්, අවශ්‍යයි'
DEFAULT_NO='නැහැ, අවශ්‍ය නැහැ'


def validate(value):
    if not isinstance(value,dict) or not {'voice_id','revision','charge','question','yes','no'}<=set(value) or set(value)-{'voice_id','revision','charge','question','yes','no','voice_title','collect_delivery'}:raise HTTPException(422,'Select a Saved Voice, service charge and Yes/No wording')
    if 'collect_delivery' in value and type(value['collect_delivery']) is not bool:raise HTTPException(422,'Choose whether to collect delivery details')
    if 'voice_title' in value and (not isinstance(value['voice_title'],str) or len(value['voice_title'])>100):raise HTTPException(422,'Invalid recording title')
    if any(type(value[key]) is not int or not 1<=value[key]<=2147483647 for key in ['voice_id','revision']):raise HTTPException(422,'Select the Saved Voice and its current revision')
    if not isinstance(value['charge'],str) or not re.fullmatch(r'\d{1,7}(?:\.\d{1,2})?',value['charge']) or Decimal(value['charge'])>Decimal('1000000'):raise HTTPException(422,'Service charge must be a non-negative LKR amount, up to two decimal places')
    if not isinstance(value['question'],str) or not 1<=len(value['question'].strip())<=900:raise HTTPException(422,'Service question must be 1–900 characters')
    for key in ['yes','no']:
        if not isinstance(value[key],str) or not 1<=len(value[key])<=20 or value[key]!=value[key].strip() or any(ord(c)<32 for c in value[key]):raise HTTPException(422,'Service reply labels must be 1–20 characters')
    if value['yes']==value['no']:raise HTTPException(422,'Use different Yes and No labels')


def recording(db,config,lock=False):
    query=select(SavedVoice).where(SavedVoice.id==config['voice_id'])
    voice=db.execute(query.with_for_update(read=True) if lock else query).scalar_one_or_none()
    if voice is None or not voice.active or voice.revision!=config['revision'] or voice.processing_status!='READY' or voice.normalization_version!=NORMALIZATION_VERSION:
        raise HTTPException(409,'Service recording changed or is not ready. Preview and select its current revision before saving/starting.')
    return voice


def verify_recordings(db,definition):
    for step in definition['steps']:
        if step.get('service'):
            voice=recording(db,step['service']);step['service']['voice_title']=voice.title


def pending(run):
    step=run.definition['steps'][run.step]
    answer=run.answers.get(step['key'])
    return bool(step.get('service') and isinstance(answer,dict) and answer.get('product_id') and 'greasing_service' not in answer)


def claim(db,run):
    # -2 is the explanation voice; -3 is the follow-up Yes/No question.
    for position in [-2,-3]:
        item=db.query(Item).filter_by(run_id=run.id,step=run.step,position=position).one_or_none()
        if item is None:db.add(Item(run_id=run.id,step=run.step,position=position,status='SENDING'));return True
        if item.status=='QUEUED':item.status='SENDING';return True
        if item.status!='SENT':run.status='UNCERTAIN';run.version+=1;return False
    run.status='UNCERTAIN';run.version+=1;return False


def fail(db,run,item,reason):
    from customer_questions import audit
    item.status='FAILED';item.reason=reason;run.status='UNCERTAIN';run.version+=1;run.updated_at=datetime.utcnow()
    audit(db,run,'SERVICE_FAILED','System',stage=item.position,reason=reason)


def send_claimed(db,run,conversation,item):
    if item.position==-2:send_explanation(db,run,conversation,item)
    else:send_question(db,run,conversation,item)


def send_explanation(db,run,conversation,item):
    from customer_questions import audit
    from quick_message_service import require_reply_window
    config=run.definition['steps'][run.step]['service']
    try:
        voice=recording(db,config,True)
        raw=library.read_audio(voice);duration=voice.duration_seconds;variant=ffmpeg_encoder_enabled()
        # Identical existing staff Saved Voice profile/flag; never prepare or mutate the library item.
        if variant:raw,duration=normalize_voice_audio(raw)
        snapshot={'storage_name':library.write_audio(raw),'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw),
            'voice_id':voice.id,'revision':voice.revision,'title':voice.title,'duration':duration,'staff_variant_c':variant}
        item.audio_snapshot=snapshot
        media=upload_whatsapp_audio(raw)
        if not isinstance(media,str) or not media.strip():fail(db,run,item,'Service voice upload failed. No follow-up question was sent.');return
        db.refresh(conversation);require_reply_window(db,conversation)
        outcome,provider=send_whatsapp_audio(conversation.phone_number,media,voice=not variant,return_outcome=True)
    except (HTTPException,VoiceAudioError):
        fail(db,run,item,'Service recording is unavailable, changed, failed validation or the reply window closed. No automatic retry.');return
    except Exception:
        item.status='UNCERTAIN';item.reason='Voice delivery is uncertain. Check chat before resending.';run.status='UNCERTAIN';run.version+=1;return
    if outcome!='SENT' or not isinstance(provider,str) or not provider.strip():
        fail(db,run,item,'Voice delivery was not confirmed. Check chat; no automatic retry.');return
    item.status='SENT';item.provider_id=provider
    db.add(Message(phone_number=conversation.phone_number,direction='outbound',message_text=f'{voice.title}\n{voice.transcript}',
        whatsapp_message_id=provider,response_kind='question_voice',send_status='sent',analytics_actor_kind='automatic',
        media_id=media,media_mime_type='audio/ogg',media_duration_seconds=duration))
    run.status='QUEUED';run.version+=1;run.updated_at=datetime.utcnow();conversation.last_message_at=max(conversation.last_message_at or run.updated_at,run.updated_at)
    audit(db,run,'SERVICE_VOICE_SENT','System',voice_id=voice.id,revision=voice.revision,provider_id=provider)


def send_question(db,run,conversation,item):
    from customer_questions import audit,send_buttons
    config=run.definition['steps'][run.step]['service']
    explanation=db.query(Item).filter_by(run_id=run.id,step=run.step,position=-2,status='SENT').one_or_none()
    if explanation is None:fail(db,run,item,'Service explanation has not been sent.');return
    audio=db.query(Message).filter_by(phone_number=conversation.phone_number,whatsapp_message_id=explanation.provider_id,response_kind='question_voice').one_or_none()
    if audio is None or audio.send_status=='failed':fail(db,run,item,'Service voice delivery failed. Staff review required.');return
    body=f"{config['question']}\n\nසේවා ගාස්තුව (LKR): {Decimal(config['charge']):,.2f}"
    buttons=[{'id':f'ppq:{run.token}:{run.step}:{i}','title':config[key]} for i,key in enumerate(['yes','no'])]
    provider=send_buttons(conversation.phone_number,body,buttons)
    if not provider:fail(db,run,item,'Service question delivery is uncertain. Check chat; no automatic retry.');return
    item.status='SENT';item.provider_id=provider;run.provider_id=provider;run.status='WAITING';run.updated_at=datetime.utcnow();run.version+=1
    db.add(Message(phone_number=conversation.phone_number,direction='outbound',message_text=body,whatsapp_message_id=provider,
        response_kind='customer_question',send_status='sent',analytics_actor_kind='automatic'))
    conversation.last_message_at=max(conversation.last_message_at or run.updated_at,run.updated_at)
    audit(db,run,'SENT','System',format='greasing_service',options=[b['title'] for b in buttons],provider_id=provider)


def receive(db,run,incoming,choice):
    from customer_questions import audit
    if incoming.response_kind!='question_reply' or choice not in {0,1}:return
    item=db.query(Item).filter_by(run_id=run.id,step=run.step,position=-3,status='SENT').one_or_none()
    if item is None or item.provider_id!=run.provider_id:return
    step=run.definition['steps'][run.step];selected=dict(run.answers[step['key']]);config=step['service']
    price=Decimal(selected['price']);charge=Decimal(config['charge']) if choice==0 else Decimal('0')
    decision={'accepted':choice==0,'charge':f'{charge:.2f}','quoted_total':f'{price+charge:.2f}','currency':'LKR',
        'voice_id':config['voice_id'],'voice_revision':config['revision']}
    selected['greasing_service']=decision;run.answers={**run.answers,step['key']:selected}
    if config.get('collect_delivery'):
        from question_delivery_details import begin
        begin(run)
    else:run.step=len(run.definition['steps']);run.status='COMPLETE'
    run.updated_at=datetime.utcnow();run.version+=1
    audit(db,run,'GREASING_SERVICE_DECIDED','Customer',**decision,inbound_id=incoming.id)
    if run.status=='COMPLETE':audit(db,run,'COMPLETE','System',note='Service decision saved. Staff can review the quoted total; no payment message or order was sent.')


def playback(db,conversation_id,provider):
    from models import CustomerQuestionRun as Run
    item=db.query(Item).join(Run,Run.id==Item.run_id).filter(Run.conversation_id==conversation_id,Item.position==-2,Item.provider_id==provider,Item.status=='SENT').one_or_none()
    if item is None or not item.audio_snapshot:raise HTTPException(404,'Service voice snapshot is unavailable')
    snap=item.audio_snapshot;raw=library._read_file(snap['storage_name'])
    if len(raw)!=snap['size_bytes'] or hashlib.sha256(raw).hexdigest()!=snap['sha256']:raise HTTPException(409,'Sent audio snapshot failed integrity validation')
    return raw
