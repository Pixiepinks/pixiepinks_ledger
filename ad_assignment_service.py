"""Local, administrator-configured ad routing. No network, sends or AI decisions."""
from datetime import datetime
import hashlib
import json
import re
from fastapi import HTTPException
from sqlalchemy import func
from models import (AdAssignmentRule as Rule, AdAssignmentRuleAudit as RuleAudit, User,
    WhatsAppConversation as Conversation, WhatsAppConversationMessage as Message,
    WhatsAppMessageReferral as Referral, ConversationAssignmentAudit as Audit)
from staff_access import allowed

BATCH=200


def ad_id(value):
    return value if isinstance(value,str) and re.fullmatch(r'[0-9]{1,40}',value) else None


def eligible(user):
    return bool(user and user.is_active and user.role in {'ADMIN','STAFF','SALES_MANAGER','SALES_AGENT'} and allowed(user,'whatsapp'))


def target(db,uid):
    row=db.query(User).filter_by(id=uid).with_for_update().one_or_none()
    if not eligible(row):raise HTTPException(422,'Choose an active staff account with WhatsApp Inbox access')
    return row


def values(rule):
    return {'staff_user_id':rule.staff_user_id,'enabled':rule.enabled,'label':rule.label}


def save(db,actor,aid,body):
    if actor.role!='ADMIN':raise HTTPException(403,'Only administrators can manage ad assignments')
    if not ad_id(aid):raise HTTPException(422,'Provide the numeric Meta Ad ID')
    if not isinstance(body,dict) or set(body)!={'staff_user_id','enabled','label','expected_version'}:
        raise HTTPException(422,'Provide staff, enabled, label and expected_version')
    uid=body['staff_user_id'];version=body['expected_version'];label=body['label']
    if type(uid) is not int or uid<1 or type(version) is not int or version<0 or type(body['enabled']) is not bool or not isinstance(label,str) or len(label)>120 or any(ord(c)<32 for c in label):
        raise HTTPException(422,'Invalid rule values')
    rule=db.query(Rule).filter_by(ad_id=aid).with_for_update().one_or_none()
    if (rule.version if rule else 0)!=version:raise HTTPException(409,'Rule changed. Reload before saving')
    # Disabled rules can be retained even after their former staff account loses access.
    if body['enabled']:target(db,uid)
    elif db.get(User,uid) is None:raise HTTPException(422,'Choose an existing staff account')
    before=values(rule) if rule else None
    after={'staff_user_id':uid,'enabled':body['enabled'],'label':label.strip()}
    if before==after:return rule
    now=datetime.utcnow()
    if rule is None:
        rule=Rule(ad_id=aid,**after,created_by_user_id=actor.id,updated_by_user_id=actor.id,version=1,created_at=now,updated_at=now)
        db.add(rule);db.flush()
    else:
        changed=db.query(Rule).filter_by(id=rule.id,version=version).update({**after,'version':version+1,
            'updated_by_user_id':actor.id,'updated_at':now},synchronize_session=False)
        if not changed:raise HTTPException(409,'Rule changed. Reload before saving')
        db.refresh(rule)
    db.add(RuleAudit(rule_id=rule.id,actor_id=actor.id,version=rule.version,changes={'before':before,'after':after}))
    db.flush();return rule


def latest(db,phone):
    return db.query(Referral,Message).join(Message,Referral.message_id==Message.id).filter(
        Message.phone_number==phone,Message.direction=='inbound',func.lower(Referral.source_type)=='ad').order_by(Message.created_at.desc(),Message.id.desc()).first()


def assign(db,conversation,rule,actor_id,source):
    if conversation.assigned_to_user_id is not None:return False
    version=conversation.assignment_version
    changed=db.query(Conversation).filter_by(id=conversation.id,assigned_to_user_id=None,assignment_version=version).update({
        'assigned_to_user_id':rule.staff_user_id,'assigned_by_user_id':actor_id,'assigned_at':datetime.utcnow(),
        'assignment_version':version+1},synchronize_session=False)
    if not changed:return False
    db.add(Audit(conversation_id=conversation.id,actor_id=actor_id,previous_owner_id=None,new_owner_id=rule.staff_user_id,
                 source=source,ad_id=rule.ad_id,rule_id=rule.id,rule_version=rule.version))
    db.flush();db.refresh(conversation)
    import staff_notifications
    staff_notifications.assignment(db,conversation,actor_id)
    return True


def inbound(db,conversation,message,metadata,verified=False):
    if not verified or not isinstance(metadata,dict) or message.direction!='inbound' or not isinstance(metadata.get('source_type'),str) or metadata['source_type'].lower()!='ad':return False
    aid=ad_id(metadata.get('source_id'))
    if not aid:return False
    # Match the exact saved message/referral, never guessed product/creative text.
    conversation=db.query(Conversation).filter_by(id=conversation.id).populate_existing().with_for_update().one()
    if conversation.assigned_to_user_id is not None:return False
    newest=latest(db,conversation.phone_number)
    if newest is None or newest[1].id!=message.id or newest[0].source_id!=aid:return False
    rule=db.query(Rule).filter_by(ad_id=aid,enabled=True).with_for_update().one_or_none()
    if rule is None:return False
    user=db.query(User).filter_by(id=rule.staff_user_id).with_for_update().one_or_none()
    if not eligible(user):return False
    return assign(db,conversation,rule,rule.updated_by_user_id,'AD_RULE')


def candidates(db,aid):
    ranked=db.query(Referral.source_id.label('ad_id'),Message.phone_number.label('phone'),Message.id.label('message_id'),
        func.row_number().over(partition_by=Message.phone_number,order_by=(Message.created_at.desc(),Message.id.desc())).label('rank')).join(
            Message,Referral.message_id==Message.id).filter(Message.direction=='inbound',func.lower(Referral.source_type)=='ad').subquery()
    return db.query(Conversation,ranked.c.message_id).join(ranked,ranked.c.phone==Conversation.phone_number).filter(
        ranked.c.rank==1,ranked.c.ad_id==aid,Conversation.assigned_to_user_id.is_(None)).order_by(Conversation.id)


def preview(db,rule):
    query=candidates(db,rule.ad_id);total=query.count();rows=query.limit(BATCH).all()
    payload=[(c.id,c.assignment_version,mid) for c,mid in rows]
    digest=hashlib.sha256(json.dumps([rule.id,rule.version,payload],separators=(',',':')).encode()).hexdigest()
    return {'total':total,'batch_size':len(rows),'limit':BATCH,'preview_token':digest,
            'conversations':[{'id':c.id,'name':c.customer_name or 'WhatsApp customer'} for c,_ in rows]}


def apply_existing(db,actor,aid,body):
    if actor.role!='ADMIN':raise HTTPException(403,'Only administrators can apply ad assignments')
    if not isinstance(body,dict) or set(body)!={'expected_version','preview_token'} or type(body['expected_version']) is not int or not isinstance(body['preview_token'],str) or not re.fullmatch(r'[a-f0-9]{64}',body['preview_token']):
        raise HTTPException(422,'Provide a preview and current rule version')
    rule=db.query(Rule).filter_by(ad_id=aid).one_or_none()
    if rule is None:raise HTTPException(404,'Rule not found')
    before=preview(db,rule)
    # Lock conversations first, matching live inbound/manual assignment lock order.
    ids=[c['id'] for c in before['conversations']]
    locked=db.query(Conversation).filter(Conversation.id.in_(ids)).order_by(Conversation.id).populate_existing().with_for_update().all()
    rule=db.query(Rule).filter_by(id=rule.id).populate_existing().with_for_update().one()
    if rule.version!=body['expected_version'] or preview(db,rule)['preview_token']!=body['preview_token']:
        raise HTTPException(409,'The rule or matching conversations changed. Preview again')
    if not rule.enabled:raise HTTPException(409,'Enable the rule before applying it')
    target(db,rule.staff_user_id)
    changed=sum(assign(db,c,rule,actor.id,'AD_RULE_EXISTING') for c in locked)
    return {'assigned':changed,'remaining':candidates(db,aid).count()}
