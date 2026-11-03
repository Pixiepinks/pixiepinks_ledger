"""Shared database-backed WhatsApp ownership policy. ADMIN is the only override."""
import re
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import func
from models import WhatsAppConversation,User,ConversationAssignmentAudit
from staff_access import allowed


def scope(query,user):
    return query if user.role=='ADMIN' else query.filter(WhatsAppConversation.assigned_to_user_id==user.id)


def require_owned(db,user,cid):
    row=scope(db.query(WhatsAppConversation),user).filter(WhatsAppConversation.id==cid).populate_existing().one_or_none()
    if row is None:raise HTTPException(404,'Conversation not found or no longer assigned to you')
    return row


def enforce_path(db,user,path,params):
    db.info['inbox_user']=user
    if user.role=='ADMIN':return
    match=re.match(r'^/crm/(?:whatsapp/api/conversations|leads/api/whatsapp)/(\d+)(?:/|$)',path)
    cid=int(match.group(1)) if match else None
    if path=='/crm/whatsapp' and params.get('conversation','').isdigit():cid=int(params['conversation'])
    if cid is not None:require_owned(db,user,cid)


def unread(db,user):
    return scope(db.query(func.coalesce(func.sum(WhatsAppConversation.unread_count),0)),user).scalar() or 0


def owners(db,conversations):
    ids={row.assigned_to_user_id for row in conversations if row.assigned_to_user_id is not None}
    return {user.id:user for user in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}


def owner_data(db,row,users=None):
    owner=(users.get(row.assigned_to_user_id) if users is not None else db.get(User,row.assigned_to_user_id)) if row.assigned_to_user_id else None
    return {'id':owner.id,'username':owner.username,'active':owner.is_active} if owner else None


def assign(db,actor,cid,owner_id,expected_version):
    if actor.role!='ADMIN':raise HTTPException(403,'Only administrators can assign conversations')
    row=db.query(WhatsAppConversation).filter_by(id=cid).populate_existing().with_for_update().one_or_none()
    if not row:raise HTTPException(404,'Conversation not found')
    if row.assignment_version!=expected_version:raise HTTPException(409,'Assignment changed. Refresh before assigning again')
    if owner_id is not None:
        target=db.query(User).filter_by(id=owner_id).with_for_update().one_or_none()
        if not target or not target.is_active or target.role not in {'ADMIN','STAFF','SALES_AGENT','SALES_MANAGER'} or not allowed(target,'whatsapp'):
            raise HTTPException(422,'Choose an active account with WhatsApp Inbox access')
    previous=row.assigned_to_user_id
    if previous==owner_id:return row
    changed=db.query(WhatsAppConversation).filter_by(id=cid,assignment_version=expected_version).update({
        'assigned_to_user_id':owner_id,'assigned_by_user_id':actor.id,'assigned_at':datetime.utcnow(),
        'assignment_version':expected_version+1},synchronize_session=False)
    if not changed:db.rollback();raise HTTPException(409,'Assignment changed. Refresh before assigning again')
    db.add(ConversationAssignmentAudit(conversation_id=cid,actor_id=actor.id,previous_owner_id=previous,new_owner_id=owner_id))
    db.commit();db.refresh(row);return row
