"""Transactional ad triggers delegate all actual execution and sends to customer_questions."""
from datetime import datetime,timedelta
import re
import unicodedata
from fastapi import HTTPException
from sqlalchemy import select,func
from models import (AdFlowControl as Control,AdFlowRule as Rule,AdFlowExecution as Execution,AdFlowAudit as Audit,
    CustomerQuestionFlow as Flow,CustomerQuestionRun as Run,CustomerQuestionConfig as QuestionConfig,
    WhatsAppConversation as Conversation,WhatsAppConversationMessage as Message,ProcessedWhatsAppMessage as Receipt,
    WhatsAppMessageReferral as Referral,CustomerQuestionProductSend as Product,User)
import customer_questions as questions

STOPS={'stop','cancel','stop questions','unsubscribe','remove me','no promotions','stop please','please unsubscribe',
       'do not send promotions','නවත්වන්න','ප්‍රවර්ධන පණිවිඩ එවන්න එපා','நிறுத்து','விளம்பர செய்திகள் அனுப்ப வேண்டாம்'}
REASONS={
 'MASTER_OFF':'Ad-based automation is OFF.','QUESTIONS_OFF':'Customer Question Automation is OFF.',
 'DISABLED':'Mapping is disabled or deleted.','SIGNATURE_REQUIRED':'Verified webhook signature required.',
 'FLOW_UNAVAILABLE':'Saved flow is missing or archived.','FLOW_CHANGED':'Saved flow changed; edit and approve the mapping again.',
 'INVALID_FLOW':'Saved flow or its media resources could not be validated.','ADMIN_UNAVAILABLE':'Configuring administrator is unavailable.',
 'NOT_NEW':'Policy A only starts on the first inbound message of a newly created CRM conversation.',
 'ACTIVE_FLOW':'Another saved flow already owns this conversation.','COOLDOWN':'The repeat cooldown has not expired.',
 'HUMAN_TAKEOVER':'Staff took over this conversation.','STAFF_OWNED':'Assigned conversations are blocked by this mapping.',
 'OPTED_OUT':'Customer stopped automatic ad flows.','WINDOW_CLOSED':'WhatsApp reply window is closed.',
 'OTHER_AUTOMATION':'An existing product workflow is active.','START_REJECTED':'The existing flow engine rejected the start.'}


def normalized_stop(text):
    text=unicodedata.normalize('NFKC',text or '')
    text=''.join(c for c in text if c not in '\u200b\u200c\u200d\ufeff')
    return ' '.join(text.casefold().split()).rstrip('.!') in STOPS


def control(db,lock=False):
    q=select(Control).where(Control.id==1)
    return db.execute(q.with_for_update() if lock else q).scalar_one()


def lock(db):
    questions.write_transaction(db)
    return questions.config(db,True,optional=True)


def event(db,user,action,rule=None,**detail):
    db.add(Audit(actor_id=user.id,rule_id=rule.id if rule else None,action=action,detail=detail))


def flow_error(db,rule):
    flow=db.get(Flow,rule.flow_id)
    if flow is None or flow.archived:return 'FLOW_UNAVAILABLE'
    if flow.version!=rule.flow_version:return 'FLOW_CHANGED'
    try:
        import copy
        definition=copy.deepcopy(flow.definition)
        questions.validate(definition)
        from question_greasing_service import verify_recordings
        verify_recordings(db,definition)
    except (HTTPException,ValueError,KeyError,TypeError):return 'INVALID_FLOW'
    return None


def eligibility(db,c,rule,is_new,now=None):
    now=now or datetime.utcnow()
    if not control(db).enabled:return 'MASTER_OFF'
    cfg=questions.config(db,optional=True)
    if not cfg or not cfg.enabled:return 'QUESTIONS_OFF'
    if not rule.enabled or rule.deleted:return 'DISABLED'
    if c.ad_flow_opted_out:return 'OPTED_OUT'
    if c.mode=='HUMAN':return 'HUMAN_TAKEOVER'
    if c.assigned_to_user_id is not None and not rule.allow_assigned:return 'STAFF_OWNED'
    active=questions.latest(db,c.id)
    if active and active.status not in questions.TERMINAL:return 'ACTIVE_FLOW'
    if c.workflow_state:return 'OTHER_AUTOMATION'
    if rule.policy=='FIRST' and not is_new:return 'NOT_NEW'
    if rule.policy=='REPEAT':
        previous=db.query(Execution).filter_by(conversation_id=c.id,status='STARTED').order_by(Execution.created_at.desc()).first()
        if previous and previous.created_at+timedelta(hours=rule.cooldown_hours)>now:return 'COOLDOWN'
    from quick_message_service import require_reply_window
    try:require_reply_window(db,c)
    except HTTPException:return 'WINDOW_CLOSED'
    return flow_error(db,rule)


def record(db,c,message,rule,status,reason=None,run=None):
    entry=Execution(rule_id=rule.id,rule_version=rule.version,message_id=message.id,conversation_id=c.id,
                    status=status,reason=reason,run_id=run.id if run else None)
    db.add(entry);db.flush();return entry


def inbound(db,c,message,metadata,is_new=False,verified=False):
    # Called after normal customer STOP/consent handling, in the original receipt transaction.
    if message.direction!='inbound':return False
    if verified and message.response_kind is None and ' '.join((message.message_text or '').casefold().split())=='start ad flows':
        c.ad_flow_opted_out=False
        questions.audit(db,None,'AD_OPT_IN','Customer',conversation_id=c.id)
        return True
    if verified and message.response_kind is None and normalized_stop(message.message_text):
        c.ad_flow_opted_out=True;stop(db,c,'Customer');return True
    existing=questions.latest(db,c.id)
    owns=bool(existing and db.query(Execution.id).filter_by(run_id=existing.id).first() and existing.status not in questions.TERMINAL)
    if owns:guard_run(db,existing,c)
    if not isinstance(metadata,dict) or not isinstance(metadata.get('source_type'),str) or metadata['source_type'].lower()!='ad':return owns
    aid=metadata.get('source_id')
    if not isinstance(aid,str) or not re.fullmatch(r'[0-9]{1,40}',aid):return owns
    if db.query(Execution.id).filter_by(message_id=message.id).first():return owns
    rule=db.query(Rule).filter_by(ad_id=aid,deleted=False).with_for_update().one_or_none()
    if rule is None:return owns
    if not verified:
        record(db,c,message,rule,'SKIPPED','SIGNATURE_REQUIRED');return owns
    newest=db.query(Referral,Message).join(Message,Referral.message_id==Message.id).filter(
        Message.phone_number==c.phone_number,Message.direction=='inbound',func.lower(Referral.source_type)=='ad').order_by(Message.created_at.desc(),Message.id.desc()).first()
    if not newest or newest[1].id!=message.id:return owns
    reason=eligibility(db,c,rule,is_new)
    if reason:
        record(db,c,message,rule,'FAILED' if reason in {'FLOW_UNAVAILABLE','FLOW_CHANGED','INVALID_FLOW'} else 'SKIPPED',reason)
        return owns
    actor=db.get(User,rule.updated_by)
    if not actor or not actor.is_active or actor.role!='ADMIN':
        record(db,c,message,rule,'FAILED','ADMIN_UNAVAILABLE');return owns
    try:
        with db.begin_nested():run=questions.start(db,c,actor,rule.flow_id,rule.flow_version)
    except HTTPException:
        record(db,c,message,rule,'FAILED','START_REJECTED');return owns
    record(db,c,message,rule,'STARTED',run=run)
    questions.audit(db,run,'AD_START','System',ad_id=aid,rule_id=rule.id,rule_version=rule.version,message_id=message.id)
    return True


def guard_run(db,run,c):
    """Question config lock must already be held. Called before sends/advancement/resume."""
    entry=db.query(Execution).filter_by(run_id=run.id).one_or_none()
    if entry is None:return True
    rule=db.get(Rule,entry.rule_id)
    reason=('MASTER_OFF' if not control(db).enabled else 'DISABLED' if not rule or not rule.enabled or rule.deleted else
            'OPTED_OUT' if c.ad_flow_opted_out else 'HUMAN_TAKEOVER' if c.mode=='HUMAN' else
            'STAFF_OWNED' if c.assigned_to_user_id is not None and not rule.allow_assigned else None)
    if reason and run.status not in questions.TERMINAL:
        desired='STOPPED' if reason=='OPTED_OUT' else 'PAUSED'
        if run.status==desired and entry.reason==reason:return False
        entry.reason=reason
        # Config lock fences HTTP; SENDING here is a claim not yet sent.
        if run.status=='SENDING':db.query(Product).filter_by(run_id=run.id,status='SENDING').update({'status':'QUEUED'},synchronize_session=False)
        run.status='STOPPED' if reason=='OPTED_OUT' else 'PAUSED';run.version+=1;run.updated_at=datetime.utcnow()
        questions.audit(db,run,'AD_GUARD','System',reason=reason)
    if reason is None and entry.reason in {'MASTER_OFF','DISABLED','HUMAN_TAKEOVER','STAFF_OWNED'}:entry.reason=None
    return reason is None


def stop(db,c,actor):
    run=questions.latest(db,c.id,True)
    if run and run.status not in questions.TERMINAL and db.query(Execution.id).filter_by(run_id=run.id).first():
        run.status='STOPPED';run.version+=1;run.updated_at=datetime.utcnow()
        questions.audit(db,run,'AD_STOP',actor,reason='Staff takeover or customer stop')


def pause_mapped(db,rule_id=None):
    query=db.query(Run).join(Execution,Execution.run_id==Run.id).filter(Run.status.notin_(questions.TERMINAL))
    if rule_id is not None:query=query.filter(Execution.rule_id==rule_id)
    for run in query.with_for_update().all():guard_run(db,run,db.get(Conversation,run.conversation_id))


def blocks_ai(db,message_id,lock_config=False):
    cfg=db.execute(select(QuestionConfig.id).where(QuestionConfig.id==1).with_for_update(read=True) if lock_config else
                   select(QuestionConfig.id).where(QuestionConfig.id==1)).first()
    if cfg is None:return False
    phone=db.query(Receipt.sender_phone).filter_by(message_id=message_id).scalar()
    if phone is None:return False
    phone=re.sub(r'\D','',phone)
    return db.query(Execution.id).join(Run,Execution.run_id==Run.id).join(Conversation,Run.conversation_id==Conversation.id).filter(
        Conversation.phone_number==phone,(Run.status.notin_(questions.TERMINAL)) | (Conversation.mode=='HUMAN')).first() is not None


def inbox(db,c):
    entry=db.query(Execution).join(Run,Execution.run_id==Run.id).filter(Execution.conversation_id==c.id,Run.status.notin_(questions.TERMINAL)).order_by(Execution.id.desc()).first()
    if entry is None:entry=db.query(Execution).filter_by(conversation_id=c.id).order_by(Execution.id.desc()).first()
    if entry is None:
        return {'ad_id':None,'title':'Ad-based flows','status':'OPTED_OUT','opted_out':True,'run_version':None} if c.ad_flow_opted_out else None
    rule=db.get(Rule,entry.rule_id);run=db.get(Run,entry.run_id) if entry.run_id else None
    flow=db.get(Flow,rule.flow_id)
    return {'ad_id':rule.ad_id,'flow_id':run.flow_id if run else rule.flow_id,'title':run.definition['title'] if run else flow.definition['title'] if flow else 'Unavailable flow',
        'status':run.status if run else entry.status,'reason':entry.reason,'started_at':entry.created_at.isoformat()+'Z' if run else None,
        'step':run.definition['steps'][run.step]['key'] if run and run.step<len(run.definition['steps']) else None,
        'run_version':run.version if run else None,'mode':c.mode,'opted_out':c.ad_flow_opted_out}
