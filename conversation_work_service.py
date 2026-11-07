"""Conversation workload overrides; never sends or edits leads, ownership or consent."""
import hashlib
import hmac
import base64
import json
import re
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,func
from fastapi import HTTPException
from models import ConversationWorkState as State,ConversationWorkEvent as Event,WhatsAppConversation as C,WhatsAppConversationMessage as M,User
from settings import settings
import conversation_access
from staff_access import allowed

STATUSES={'resolve':'RESOLVED','defer':'FOLLOW_UP_LATER','reopen':'NEEDS_REPLY','cancel':'NEEDS_REPLY'}


def iso(value):return value.isoformat()+'Z' if value else None


def latest(db,phone,exclude_id=None):
    q=db.query(M).filter_by(phone_number=phone,direction='inbound')
    if exclude_id is not None:q=q.filter(M.id!=exclude_id)
    return q.order_by(M.created_at.desc(),M.id.desc()).first()


def ensure(db,c,exclude_id=None):
    from crm_analytics_tracking import insert_for
    current=latest(db,c.phone_number,exclude_id)
    db.execute(insert_for(db.connection(),State.__table__).values(conversation_id=c.id,status='NEEDS_REPLY',version=0,
        updated_at=c.created_at or datetime.utcnow(),force_reply=False,baseline_id=0,ack_inbound_id=0,inbound_id=current.id if current else None,
        inbound_at=current.created_at if current else None).on_conflict_do_nothing(index_elements=['conversation_id']))
    return db.query(State).filter_by(conversation_id=c.id).populate_existing().one()


def snapshots(db,conversations):
    if not conversations:return {}
    ids=[c.id for c in conversations];phones=[c.phone_number for c in conversations]
    states={s.conversation_id:s for s in db.query(State).filter(State.conversation_id.in_(ids))}
    inbound_rows=db.query(M.phone_number,func.max(M.id),func.max(M.created_at)).filter(M.phone_number.in_(phones),M.direction=='inbound').group_by(M.phone_number).all()
    inbound={p:i for p,i,t in inbound_rows};inbound_times={p:t for p,i,t in inbound_rows}
    import inbox_reply_queue
    pending,_=inbox_reply_queue.summary(db)
    waiting={p for p, in db.query(pending.c.phone).filter(pending.c.phone.in_(phones))}
    now=datetime.utcnow();result={}
    for c in conversations:
        s=states.get(c.id)
        token_data=json.dumps([c.id,s.version if s else 0,inbound.get(c.phone_number),now.isoformat()],separators=(',',':')).encode()
        encoded=base64.urlsafe_b64encode(token_data).decode().rstrip('=')
        signature=hmac.new(settings.SECRET_KEY.encode(),encoded.encode(),hashlib.sha256).hexdigest()
        result[c.id]={'attention_required':c.phone_number in waiting,'review_token':encoded+'.'+signature,'status':s.status if s else 'NEEDS_REPLY','version':s.version if s else 0,
            'last_inbound_id':inbound.get(c.phone_number),'updated_at':iso(s.updated_at) if s else None,
            'due_at':iso(s.due_at) if s else None,'followup_user_id':s.followup_user_id if s else None,
            'reason':s.reason if s else None,'resolved_at':iso(s.resolved_at) if s else None,
            'window_closed':not (c.last_customer_message_at or inbound_times.get(c.phone_number)) or not timedelta(0)<=now-(c.last_customer_message_at or inbound_times[c.phone_number])<timedelta(hours=24)}
    return result


def inbound(db,c,message,qualifying=True):
    """Runs after receipt claim/message flush, in that same webhook transaction."""
    if not qualifying:return
    if message.whatsapp_message_id and db.query(M.id).filter(M.phone_number==c.phone_number,M.whatsapp_message_id==message.whatsapp_message_id,M.id!=message.id).first():return
    ensure(db,c,message.id)
    row=db.query(State).filter_by(conversation_id=c.id).populate_existing().with_for_update().one()
    # Timestamp chronology prevents old reprocessing. Meta has whole-second precision:
    # same-second arrivals conservatively reopen rather than risk hiding a new inquiry.
    if row.inbound_id==message.id:return
    cutoff=row.inbound_at
    if row.status in {'RESOLVED','FOLLOW_UP_LATER'}:
        cutoff=max(cutoff or datetime.min,(row.review_cutoff_at or row.updated_at).replace(microsecond=0))
    import inbox_reply_queue
    reply_time=db.query(func.max(M.created_at)).filter(M.phone_number==c.phone_number,M.direction=='outbound',inbox_reply_queue.successful(M)).scalar()
    if reply_time:cutoff=max(cutoff or datetime.min,reply_time.replace(microsecond=0))
    if cutoff and message.created_at<cutoff:return
    if row.inbound_id and message.id<=row.inbound_id:return
    previous=row.status;changed=previous!='NEEDS_REPLY'
    db.query(State).filter_by(conversation_id=c.id,version=row.version).update({
        'status':'NEEDS_REPLY','version':row.version+1,'inbound_id':message.id,'inbound_at':message.created_at,
        'force_reply':True,'baseline_id':db.query(func.max(M.id)).filter_by(phone_number=c.phone_number).scalar() or message.id,
        'updated_at':datetime.utcnow(),'updated_by':None,'reason':'New customer message',
        **({'due_at':None,'followup_user_id':None,'resolved_at':None} if changed else {})},synchronize_session=False)
    if changed:
        db.add(Event(conversation_id=c.id,dedup_key=f'inbound:{message.id}',fingerprint=hashlib.sha256(str(message.id).encode()).hexdigest(),
            previous_status=previous,new_status='NEEDS_REPLY',trigger='inbound',reason='New customer message',inbound_id=message.id))
    db.expire(row)


def transition(db,user,cid,payload):
    if user.role not in {'ADMIN','STAFF','SALES_AGENT','SALES_MANAGER'} or not allowed(user,'whatsapp'):raise HTTPException(403,'WhatsApp Inbox access required')
    c=conversation_access.scope(db.query(C),user).filter(C.id==cid).populate_existing().with_for_update().one_or_none()
    if c is None:raise HTTPException(404,'Conversation not found or access removed')
    if not conversation_access.permissions(db,user,c)['can_manage']:raise HTTPException(403,'Only the owner or administrator can manage conversation status')
    if not isinstance(payload,dict):raise HTTPException(422,'Invalid status action')
    action=payload.get('action');status=STATUSES.get(action)
    version=payload.get('version');expected=payload.get('last_inbound_id');key=payload.get('key');reason=payload.get('reason','')
    if status is None or type(version) is not int or version<0 or (expected is not None and (type(expected) is not int or expected<1)):
        raise HTTPException(422,'Choose a valid action and current conversation version')
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',key):raise HTTPException(422,'A valid action key is required')
    if not isinstance(reason,str) or len(reason)>1000:raise HTTPException(422,'Keep the reason within 1000 characters')
    fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    dedup=f'work:{user.id}:{key}:{cid}'
    old=db.query(Event).filter_by(dedup_key=dedup).one_or_none()
    if old:
        if old.fingerprint!=fingerprint:raise HTTPException(409,'Action key belongs to a different request')
        return snapshots(db,[c])[cid]
    try:
        encoded,signature=payload.get('review_token','').split('.')
        expected_signature=hmac.new(settings.SECRET_KEY.encode(),encoded.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected_signature):raise ValueError()
        proof=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
        reviewed=datetime.fromisoformat(proof[3])
        if proof[:3]!=[cid,version,expected] or reviewed>datetime.utcnow()+timedelta(seconds=1) or reviewed<datetime.utcnow()-timedelta(days=1):raise ValueError()
    except (ValueError,TypeError,AttributeError,IndexError):raise HTTPException(409,'Conversation review expired or changed. Refresh and review before continuing.') from None
    due=None;owner=None
    if action=='defer':
        try:
            due=datetime.fromisoformat(payload.get('due_at','').replace('Z','+00:00'))
            if due.tzinfo is None:raise ValueError()
            due=due.astimezone(timezone.utc).replace(tzinfo=None)
        except (ValueError,TypeError,AttributeError,OverflowError):raise HTTPException(422,'Choose a valid future date/time with timezone') from None
        if due<=datetime.utcnow():raise HTTPException(422,'Follow-up time must be in the future')
        owner=payload.get('followup_user_id')
        if owner is not None:
            if type(owner) is not int or (user.role!='ADMIN' and owner!=user.id):raise HTTPException(403,'Choose yourself or leave follow-up assignment empty')
            target=db.get(User,owner)
            if not target or not target.is_active or target.role not in {'ADMIN','STAFF','SALES_AGENT','SALES_MANAGER'} or not allowed(target,'whatsapp'):
                raise HTTPException(422,'Choose an active WhatsApp staff account')
            if not conversation_access.scope(db.query(C.id),target).filter(C.id==cid).first():
                raise HTTPException(422,'Follow-up assignee must already own or have shared access to this conversation')
    row=ensure(db,c)
    actual=db.query(func.max(M.id)).filter_by(phone_number=c.phone_number,direction='inbound').scalar()
    if row.version!=version or actual!=expected:raise HTTPException(409,'Conversation changed or received a message. Refresh and review before changing its status.')
    if action=='cancel' and row.status!='FOLLOW_UP_LATER':raise HTTPException(409,'This conversation has no deferred follow-up to cancel')
    if status=='RESOLVED' and row.status==status:return snapshots(db,[c])[cid]
    previous=row.status;now=datetime.utcnow();baseline=db.query(func.max(M.id)).filter_by(phone_number=c.phone_number).scalar() or 0
    changed=db.query(State).filter_by(conversation_id=cid,version=version).update({'status':status,'version':version+1,
        'updated_at':now,'updated_by':user.id,'reason':reason.strip() or None,'resolved_at':now if status=='RESOLVED' else None,
        'review_cutoff_at':reviewed.replace(microsecond=0),'ack_inbound_id':actual or 0,'due_at':due,'followup_user_id':owner,'force_reply':status=='NEEDS_REPLY','baseline_id':baseline},synchronize_session=False)
    if not changed:raise HTTPException(409,'Conversation changed. Refresh and review before changing its status.')
    db.add(Event(conversation_id=cid,dedup_key=dedup,fingerprint=fingerprint,previous_status=previous,new_status=status,
        actor_id=user.id,actor_name=user.username,trigger='manual',reason=reason.strip() or ('Follow-up canceled' if action=='cancel' else None),
        inbound_id=actual,due_at=due,followup_user_id=owner))
    db.flush();db.expire(row)
    return snapshots(db,[c])[cid]
