"""Database-backed conversation visibility and customer-reply permissions."""
import re
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import func, or_
from models import WhatsAppConversation,User,ConversationAssignmentAudit,ConversationShare
from staff_access import allowed


def shared(user, reply=False):
    condition = (ConversationShare.user_id==user.id) & (ConversationShare.conversation_id==WhatsAppConversation.id)
    if reply:condition = condition & ConversationShare.can_reply.is_(True)
    return condition


def scope(query,user):
    visible = or_(WhatsAppConversation.assigned_to_user_id==user.id,
                  ConversationShare.__table__.select().with_only_columns(ConversationShare.id).where(shared(user)).exists())
    return query if user.role=='ADMIN' else query.filter(visible)


def require_visible(db,user,cid):
    row=scope(db.query(WhatsAppConversation),user).filter(WhatsAppConversation.id==cid).populate_existing().one_or_none()
    if row is None:raise HTTPException(404,'Conversation not found or access removed')
    return row


def permissions(db,user,row):
    owner=user.role=='ADMIN' or row.assigned_to_user_id==user.id
    share=db.query(ConversationShare).filter_by(conversation_id=row.id,user_id=user.id).one_or_none() if not owner else None
    return {'can_reply':owner or bool(share and share.can_reply),'can_manage':owner}


def require_owned(db,user,cid):
    row=require_visible(db,user,cid)
    if not permissions(db,user,row)['can_reply']:raise HTTPException(403,'Shared access allows reading and private notes only')
    return row


def enforce_path(db,user,path,params,method="GET"):
    db.info['inbox_user']=user
    if user.role=='ADMIN':return
    match=re.match(r'^/crm/(?:whatsapp/api/conversations|leads/api/whatsapp)/(\d+)(?:/|$)',path)
    cid=int(match.group(1)) if match else None
    if path=='/crm/whatsapp' and params.get('conversation','').isdigit():cid=int(params['conversation'])
    if cid is not None:
        row=require_visible(db,user,cid)
        if method not in {'GET','HEAD'}:
            suffix=path.rsplit('/',1)[-1]
            if suffix in {'notes','read'}:return
            # A shared reply grant permits sends, not ownership, lead edits or AI mode changes.
            if suffix.startswith('send') or '/collection-batches' in path:
                require_owned(db,user,cid)
            elif row.assigned_to_user_id!=user.id:
                raise HTTPException(403,'Only the conversation owner or administrator can change this conversation')


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
    db.flush();db.refresh(row)
    import staff_notifications
    staff_notifications.assignment(db,row,actor.id)
    db.commit();db.refresh(row);return row
