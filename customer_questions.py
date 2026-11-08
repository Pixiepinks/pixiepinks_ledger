"""Deterministic, durable questions. No AI, marketing consent or product discovery changes."""
import json
import secrets
import logging
from datetime import datetime,timedelta
from fastapi import HTTPException
from sqlalchemy import select,update
from models import CustomerQuestionProductSend as ProductSend,CustomerQuestionFlow as Flow,CustomerQuestionConfig as Config,CustomerQuestionRun as Run,CustomerQuestionEvent as Event
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
    if not isinstance(steps,list) or not 1<=len(steps)<=50:raise HTTPException(422,'Use 1–50 flow steps')
    keys=set()
    for step in steps:
        if not isinstance(step,dict):raise HTTPException(422,'Invalid flow step')
        key=step.get('key')
        if not isinstance(key,str) or not re.fullmatch('[a-z][a-z0-9_]{0,29}',key) or key in keys:raise HTTPException(422,'Question keys must be unique lowercase names')
        keys.add(key)
        if step.get('type')=='products':
            from question_product_service import validate_selection
            if not {'key','type','products'}<=set(step) or set(step)-{'key','type','products','choose','choice_body','choice_button','service'}:raise HTTPException(422,'Product steps need a key and selected products only; they finish for staff')
            validate_selection(step['products'])
            if type(step.get('choose',False)) is not bool:raise HTTPException(422,'Choose whether to ask for a product selection')
            if step.get('choose'):
                if len(step['products'])>10:raise HTTPException(422,'A product selection list supports at most 10 selected products')
                body=step.get('choice_body','Which product do you like?');button=step.get('choice_button','Choose a product')
                if not isinstance(body,str) or not 1<=len(body.strip())<=1024:raise HTTPException(422,'Product-choice message must be 1–1024 characters')
                if not isinstance(button,str) or not 1<=len(button)<=20 or button!=button.strip() or any(ord(c)<32 for c in button):raise HTTPException(422,'Product-choice button must be 1–20 characters')
            elif 'choice_body' in step or 'choice_button' in step:raise HTTPException(422,'Enable the product selection list before setting its wording')
            if 'service' in step:
                if not step.get('choose'):raise HTTPException(422,'Enable customer product choice before adding the greasing follow-up')
                from question_greasing_service import validate as validate_service
                validate_service(step['service'])
            continue
        if not {'key','body','options'}<=set(step) or set(step)-{'key','body','options','type','list_button','next'}:raise HTTPException(422,'Each question needs a key, body and reply options')
        kind=step.get('type','buttons')
        if not isinstance(kind,str) or kind not in {'buttons','list'}:raise HTTPException(422,'Choose Buttons or List')
        if 'list_button' in step and kind!='list':raise HTTPException(422,'List button text is only available for List questions')
        label=step.get('list_button','Choose option')
        if not isinstance(label,str) or not 1<=len(label)<=20 or label!=label.strip() or any(ord(c)<32 for c in label):raise HTTPException(422,'List button text must be 1–20 characters')
        key=step['key'];body=step['body'];options=step['options']
        if not isinstance(body,str) or not 1<=len(body.strip())<=1024:raise HTTPException(422,'Question text must be 1–1024 characters')
        count,limit=(10,24) if kind=='list' else (3,20)
        if not isinstance(options,list) or not 1<=len(options)<=count or any(not isinstance(o,str) or not 1<=len(o)<=limit or o!=o.strip() or any(ord(c)<32 for c in o) for o in options):raise HTTPException(422,f'Use 1–{count} options with 1–{limit} characters each')
        if len(set(options))!=len(options):raise HTTPException(422,'Button labels must be different')
    graph={}
    for index,step in enumerate(steps):
        if step.get('type')=='products':graph[step['key']]=[];continue
        targets=step.get('next',[steps[index+1]['key'] if index+1<len(steps) else None]*len(step['options']))
        if not isinstance(targets,list) or len(targets)!=len(step['options']):raise HTTPException(422,'Choose one next step for every answer')
        if any(target is not None and (not isinstance(target,str) or target not in keys) for target in targets):raise HTTPException(422,'A next step refers to a missing question. Review the answer routes.')
        graph[step['key']]=[target for target in targets if target is not None]
    visiting=set();visited=set()
    def visit(key):
        if key in visiting:raise HTTPException(422,'Question flows cannot contain loops. Choose another next step or Finish.')
        if key in visited:return
        visiting.add(key)
        for target in graph[key]:visit(target)
        visiting.remove(key);visited.add(key)
    visit(steps[0]['key'])
    if visited!=keys:raise HTTPException(422,'Some questions cannot be reached from the first question. Connect or remove them.')
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


def snapshot(run,db=None):
    if run is None:return None
    result={'id':run.id,'flow_id':run.flow_id,'status':run.status,'step':run.step,'title':run.definition['title'],
            'answers':run.answers,'version':run.version,'updated_at':run.updated_at.isoformat()+'Z',
            'questions':len(run.definition['steps'])}
    if db is not None:
        items=db.query(ProductSend).filter(ProductSend.run_id==run.id,ProductSend.position>=0).order_by(ProductSend.position).all()
        index=items[0].step if items else run.step
        if index<len(run.definition['steps']) and run.definition['steps'][index].get('type')=='products':
            result['products']={'total':len(run.definition['steps'][index]['products']),
                'sent':sum(item.status=='SENT' for item in items),'skipped':sum(item.status=='SKIPPED' for item in items),
                'issues':[{'position':item.position+1,'status':item.status,'reason':item.reason} for item in items if item.status not in {'SENT','SENDING'}]}
            if run.definition['steps'][index].get('service'):
                stages=db.query(ProductSend).filter(ProductSend.run_id==run.id,ProductSend.step==index,ProductSend.position.in_([-2,-3])).all()
                payment_stages=db.query(ProductSend).filter(ProductSend.run_id==run.id,ProductSend.step==index,ProductSend.position.in_([-200,-201,-202])).order_by(ProductSend.position.desc()).all()
                if payment_stages:result['payment_progress']=[{'stage':{-200:'Voice',-201:'Bank image',-202:'Confirmation'}[row.position],'status':row.status,'reason':row.reason} for row in payment_stages]
                result['service']={'voice_status':next((row.status for row in stages if row.position==-2),'NOT_SENT'),
                    'question_status':next((row.status for row in stages if row.position==-3),'NOT_SENT'),
                    'charge':run.definition['steps'][index]['service']['charge'],'issues':[row.reason for row in stages if row.reason]}

    return result


def start(db,c,user,flow_id=1,flow_version=None):
    control=config(db,True)
    if not control.enabled:raise HTTPException(409,'Customer Question Automation is OFF')
    # Serialize starts per conversation, including when no run exists yet.
    db.execute(select(Conversation.id).where(Conversation.id==c.id).with_for_update()).one()
    old=latest(db,c.id,True)
    if old and old.status not in TERMINAL:raise HTTPException(409,'This customer already has a question sequence. Review it before starting another.')
    if type(flow_id) is not int or not 1<=flow_id<=2147483647:raise HTTPException(422,'Choose a saved flow')
    flow=db.execute(select(Flow).where(Flow.id==flow_id).with_for_update()).scalar_one_or_none()
    if flow is None or flow.archived:raise HTTPException(409,'This flow is unavailable or archived. Choose another flow.')
    if flow_version is not None and (type(flow_version) is not int or flow_version!=flow.version):raise HTTPException(409,'The flow changed since preview. Refresh and review before starting.')
    from question_greasing_service import verify_recordings
    if any(step.get('service') for step in flow.definition['steps']):
        from staff_access import allowed
        from quick_message_service import authorize
        authorize(user)
        if not allowed(user,'saved_voices'):raise HTTPException(403,'Saved Voices access is required to start this flow')
    verify_recordings(db,flow.definition)
    require_reply_window(db,c)
    run=Run(conversation_id=c.id,flow_id=flow.id,definition=flow.definition,status='QUEUED',step=0,answers={},
            token=secrets.token_urlsafe(24),created_by=user.username,version=0)
    db.add(run);db.flush();audit(db,run,'START',user.username,flow_id=flow.id,flow_version=flow.version)
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
    question=run.definition['steps'][step]
    earliest=run.updated_at
    if question.get('type')=='products':
        from question_payment_followup import pending as payment_pending
        if payment_pending(run):return
        from question_delivery_details import pending as delivery_pending,receive_button
        if delivery_pending(run):
            receive_button(db,run,message,choice);return
        from question_greasing_service import pending as service_pending
        invitation=db.query(ProductSend).filter_by(run_id=run.id,step=step,position=-3 if service_pending(run) else -1,status='SENT').one_or_none()
        if invitation is not None:earliest=invitation.created_at
    if message.created_at < earliest.replace(microsecond=0) or message.created_at>datetime.utcnow()+timedelta(minutes=5):return
    if question.get('type')=='products':
        from question_greasing_service import pending as service_pending,receive as receive_service
        if service_pending(run):receive_service(db,run,message,choice);return
        from question_product_service import receive_choice
        receive_choice(db,run,message,choice,c.phone_number);return
    expected='question_list' if question.get('type','buttons')=='list' else 'question_reply'
    if message.response_kind!=expected or not 0<=choice<len(question['options']):return
    # Labels and answers come from the retained configuration, never reply text.
    run.answers={**run.answers,question['key']:question['options'][choice]}
    audit(db,run,'ANSWER','Customer',key=question['key'],answer=question['options'][choice],message_id=message.id)
    if 'next' in question:
        target=question['next'][choice]
        run.step=(next(i for i,item in enumerate(run.definition['steps']) if item['key']==target)
                  if target is not None else len(run.definition['steps']))
    else:run.step+=1  # Retained legacy sequences remain sequential.
    if 'next' in question:audit(db,run,'ROUTE','System',from_key=question['key'],next_key=(run.definition['steps'][run.step]['key'] if run.step<len(run.definition['steps']) else None))
    run.provider_id=None;run.token=secrets.token_urlsafe(24)
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
        if run.definition['steps'][run.step].get('type')=='products':
            from question_product_service import claim
            if not claim(db,run):db.commit();return True
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
            db.query(ProductSend).filter_by(run_id=run.id,step=run.step,status='SENDING').update({'status':'QUEUED'},synchronize_session=False)
            run.status='PAUSED';run.version+=1;run.updated_at=datetime.utcnow()
            audit(db,run,'PAUSE','System',reason='Automation is OFF');db.commit();return True
        try:require_reply_window(db,c)
        except HTTPException:
            from question_payment_review import window_closed
            window_closed(db,run)
            run.status='WINDOW_CLOSED';run.version+=1;audit(db,run,'WINDOW_CLOSED','System');db.commit();return True
        step=run.definition['steps'][run.step]
        if step.get('type')=='products':
            from question_product_service import send_claimed
            send_claimed(db,run,c);db.commit();return True
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


def flow_snapshot(flow):
    return {'id':flow.id,'title':flow.definition['title'],'definition':flow.definition,
            'version':flow.version,'archived':flow.archived}


def library(db,include_archived=False):
    query=db.query(Flow)
    if not include_archived:query=query.filter_by(archived=False)
    return [flow_snapshot(flow) for flow in query.order_by(Flow.id).all()]
