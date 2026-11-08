"""Deterministic, durable questions. No AI, marketing consent or product discovery changes."""
import json
import secrets
import logging
from datetime import datetime,timedelta
from fastapi import HTTPException
from sqlalchemy import select,update
from models import CustomerQuestionConfig as Config,CustomerQuestionRun as Run,CustomerQuestionEvent as Event
from models import WhatsAppConversation as Conversation,WhatsAppConversationMessage as Message
from quick_message_service import require_reply_window

DEFAULT={'title':'Child preferences','steps':[
    {'key':'gender','body':'Who are you shopping for?','options':['Girl','Boy','Any']},
    {'key':'age','body':"What is the child’s age range?",'options':['Under 3 years','3–6 years','7+ years']}]}
TERMINAL={'STOPPED','COMPLETE'}
STOP_WORDS={'stop','cancel','stop questions'}
logger=logging.getLogger(__name__)


def validate(value):
    import re
    if not isinstance(value,dict) or set(value)!={'title','steps'}:raise HTTPException(422,'Provide a title and question steps')
    if not isinstance(value['title'],str) or not 1<=len(value['title'].strip())<=100:raise HTTPException(422,'Title must be 1–100 characters')
    steps=value['steps']
    if not isinstance(steps,list) or not 1<=len(steps)<=10:raise HTTPException(422,'Use 1–10 questions')
    keys=set()
    for step in steps:
        if not isinstance(step,dict) or not {'key','body','options'}<=set(step) or set(step)-{'key','body','options','type','list_button'}:raise HTTPException(422,'Each question needs a key, body and reply options')
        kind=step.get('type','buttons')
        if not isinstance(kind,str) or kind not in {'buttons','list'}:raise HTTPException(422,'Choose Buttons or List')
        if 'list_button' in step and kind!='list':raise HTTPException(422,'List button text is only available for List questions')
        label=step.get('list_button','Choose option')
        if not isinstance(label,str) or not 1<=len(label)<=20 or label!=label.strip() or any(ord(c)<32 for c in label):raise HTTPException(422,'List button text must be 1–20 characters')
        key=step['key'];body=step['body'];options=step['options']
        if not isinstance(key,str) or not re.fullmatch('[a-z][a-z0-9_]{0,29}',key) or key in keys:raise HTTPException(422,'Question keys must be unique lowercase names')
        keys.add(key)
        if not isinstance(body,str) or not 1<=len(body.strip())<=1024:raise HTTPException(422,'Question text must be 1–1024 characters')
        count,limit=(10,24) if kind=='list' else (3,20)
        if not isinstance(options,list) or not 1<=len(options)<=count or any(not isinstance(o,str) or not 1<=len(o)<=limit or o!=o.strip() or any(ord(c)<32 for c in o) for o in options):raise HTTPException(422,f'Use 1–{count} options with 1–{limit} characters each')
        if len(set(options))!=len(options):raise HTTPException(422,'Button labels must be different')
    return json.loads(json.dumps(value))


def write_transaction(db):
    connection=db.connection()
    if connection.dialect.name=='sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN IMMEDIATE')


def config(db,lock=False,optional=False):
    result=db.execute(select(Config).where(Config.id==1).with_for_update() if lock else select(Config).where(Config.id==1))
    return result.scalar_one_or_none() if optional else result.scalar_one()


def audit(db,run,action,actor,**detail):
    db.add(Event(run_id=run.id if run else None,action=action,actor=actor,detail=detail))


def latest(db,cid,lock=False):
    q=select(Run).where(Run.conversation_id==cid).order_by(Run.id.desc()).limit(1)
    return db.execute(q.with_for_update() if lock else q).scalar_one_or_none()


def snapshot(run):
    if run is None:return None
    return {'id':run.id,'status':run.status,'step':run.step,'title':run.definition['title'],
            'answers':run.answers,'version':run.version,'updated_at':run.updated_at.isoformat()+'Z',
            'questions':len(run.definition['steps'])}


def start(db,c,user):
    control=config(db,True)
    if not control.enabled:raise HTTPException(409,'Customer Question Automation is OFF')
    # Serialize starts per conversation, including when no run exists yet.
    db.execute(select(Conversation.id).where(Conversation.id==c.id).with_for_update()).one()
    old=latest(db,c.id,True)
    if old and old.status not in TERMINAL:raise HTTPException(409,'This customer already has a question sequence. Review it before starting another.')
    require_reply_window(db,c)
    run=Run(conversation_id=c.id,definition=control.definition,status='QUEUED',step=0,answers={},
            token=secrets.token_urlsafe(24),created_by=user.username,version=0)
    db.add(run);db.flush();audit(db,run,'START',user.username)
    return run


def change(db,c,user,action,version):
    config(db,True)
    run=latest(db,c.id,True)
    if not run or type(version) is not int or version!=run.version:raise HTTPException(409,'Questions changed. Refresh and review.')
    if action=='stop' and run.status not in TERMINAL:run.status='STOPPED'
    elif action=='pause' and run.status in {'QUEUED','WAITING'}:
        run.status='PAUSED'
    elif action=='resume' and run.status=='PAUSED':
        require_reply_window(db,c)
        # Keep the same already-sent question. Never resend it on resume.
        run.status='WAITING' if run.provider_id else 'QUEUED'
    else:raise HTTPException(409,'This action is unavailable. Uncertain sends must be reviewed; they cannot be retried automatically.')
    run.version+=1;run.updated_at=datetime.utcnow();audit(db,run,action.upper(),user.username)
    return run


def inbound(db,c,message,button_id):
    """Caller persists receipt, history and transition in one transaction."""
    control=config(db,True)
    run=latest(db,c.id,True)
    if not run:return
    parts=button_id.split(':')
    if len(parts)!=4 or parts[0]!='ppq' or not secrets.compare_digest(parts[1],run.token):return
    try:step=int(parts[2]);choice=int(parts[3])
    except ValueError:return
    if not control.enabled or run.status!='WAITING' or step!=run.step:return
    if message.reply_to_whatsapp_message_id!=run.provider_id:return
    if message.created_at < run.updated_at.replace(microsecond=0) or message.created_at>datetime.utcnow()+timedelta(minutes=5):return
    question=run.definition['steps'][step]
    expected='question_list' if question.get('type','buttons')=='list' else 'question_reply'
    if message.response_kind!=expected or not 0<=choice<len(question['options']):return
    # Labels and answers come from the retained configuration, never reply text.
    run.answers={**run.answers,question['key']:question['options'][choice]}
    audit(db,run,'ANSWER','Customer',key=question['key'],answer=question['options'][choice],message_id=message.id)
    run.step+=1;run.provider_id=None;run.token=secrets.token_urlsafe(24)
    run.status='COMPLETE' if run.step==len(run.definition['steps']) else 'QUEUED'
    run.updated_at=datetime.utcnow();run.version+=1
    if run.status=='COMPLETE':audit(db,run,'COMPLETE','System',note='Preferences ready for staff. This is not marketing consent.')


def stop_for_customer(db,c):
    config(db,True)
    run=latest(db,c.id,True)
    if run and run.status not in TERMINAL:
        run.status='STOPPED';run.version+=1;run.updated_at=datetime.utcnow()
        audit(db,run,'STOP','Customer')


def send_buttons(phone,body,buttons,*,list_button=None):
    """Separate deterministic transport; never enters the AI reply pipeline."""
    import httpx
    from settings import settings
    if not settings.META_WHATSAPP_ACCESS_TOKEN or not settings.META_WHATSAPP_PHONE_NUMBER_ID:
        return None
    payload={'messaging_product':'whatsapp','to':phone,'type':'interactive','interactive':{'type':'button',
        'body':{'text':body},'action':{'buttons':[{'type':'reply','reply':b} for b in buttons]}}}
    if list_button is not None:
        payload['interactive']={'type':'list','body':{'text':body},'action':{
            'button':list_button,'sections':[{'rows':buttons}]}}
    try:
        response=httpx.post(f'https://graph.facebook.com/{settings.META_GRAPH_API_VERSION.strip().strip("/") or "v26.0"}/{settings.META_WHATSAPP_PHONE_NUMBER_ID}/messages',
            headers={'Authorization':f'Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}'},json=payload,timeout=10)
        response.raise_for_status()
        provider=response.json()['messages'][0]['id']
        return provider if isinstance(provider,str) and 0<len(provider)<=255 else None
    except (httpx.HTTPError,ValueError,KeyError,IndexError,TypeError):return None


def dispatch_one(sessions):
    """Claim durably before HTTP. Crashes/timeouts are never automatically retried."""
    with sessions() as db:
        write_transaction(db)
        control=config(db,True)
        if not control.enabled:return False
        run=db.execute(select(Run).where(Run.status=='QUEUED').order_by(Run.id).limit(1).with_for_update(skip_locked=True)).scalar_one_or_none()
        if not run:return False
        rid=run.id
        claimed=db.execute(update(Run).where(Run.id==rid,Run.status=='QUEUED').values(status='SENDING',updated_at=datetime.utcnow(),version=Run.version+1)).rowcount
        if not claimed:db.rollback();return False
        audit(db,run,'SEND_CLAIM','System',step=run.step);db.commit()
    with sessions() as db:
        write_transaction(db)
        control=config(db,True)  # OFF waits for any in-flight send; no future sends after OFF returns.
        run=db.execute(select(Run).where(Run.id==rid).with_for_update()).scalar_one()
        if run.status!='SENDING':return True
        c=db.get(Conversation,run.conversation_id)
        if not control.enabled:
            run.status='PAUSED';run.version+=1;run.updated_at=datetime.utcnow()
            audit(db,run,'PAUSE','System',reason='Automation is OFF');db.commit();return True
        try:require_reply_window(db,c)
        except HTTPException:
            run.status='WINDOW_CLOSED';run.version+=1;audit(db,run,'WINDOW_CLOSED','System');db.commit();return True
        step=run.definition['steps'][run.step]
        buttons=[{'id':f'ppq:{run.token}:{run.step}:{i}','title':label} for i,label in enumerate(step['options'])]
        provider=(send_buttons(c.phone_number,step['body'],buttons,list_button=step.get('list_button','Choose option'))
            if step.get('type','buttons')=='list' else send_buttons(c.phone_number,step['body'],buttons))
        when=datetime.utcnow()
        run.status='WAITING' if provider else 'UNCERTAIN';run.provider_id=provider;run.updated_at=when;run.version+=1
        if provider:
            db.add(Message(phone_number=c.phone_number,direction='outbound',message_text=step['body'],
                whatsapp_message_id=provider,response_kind='customer_question',send_status='sent',
                analytics_actor_kind='automatic',created_at=when))
            c.last_message_at=max(c.last_message_at or when,when)
        audit(db,run,'SENT' if provider else 'UNCERTAIN','System',step=run.step,
              options=step['options'],provider_id=provider,format=step.get('type','buttons'))
        db.commit()
    return True


def worker(sessions,stop):
    while not stop.is_set():
        try:
            if dispatch_one(sessions):continue
        except Exception:
            # Fixed diagnostic only; no customer payloads or tokens.
            logger.error('Customer question worker failed; claimed sends will not be retried')
        stop.wait(2)
