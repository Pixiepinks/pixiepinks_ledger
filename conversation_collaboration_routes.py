"""CRM-only collaboration. This module never calls a customer messaging service."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from models import (User, WhatsAppConversation, ConversationShare, ConversationShareAudit,
                    ConversationNote, ConversationMention)
from staff_access import allowed
import conversation_access as access


def eligible(user):
    return user.is_active and user.role in {'ADMIN','STAFF','SALES_AGENT','SALES_MANAGER'} and allowed(user,'whatsapp')


def participants(db, row):
    shared_ids=db.query(ConversationShare.user_id).filter_by(conversation_id=row.id)
    return [u for u in db.query(User).filter(or_(User.role=='ADMIN',User.id==row.assigned_to_user_id,User.id.in_(shared_ids))).order_by(User.username) if eligible(u)]


def note_data(db,note):
    author=db.get(User,note.author_id)
    mentions=db.query(User.id,User.username).join(ConversationMention,ConversationMention.user_id==User.id).filter(ConversationMention.note_id==note.id).all()
    return {'id':note.id,'author':author.username,'body':note.body,'created_at':note.created_at.isoformat()+'Z',
            'mentions':[{'id':u.id,'username':u.username} for u in mentions]}


def commit(db):
    try:db.commit()
    except SQLAlchemyError:
        db.rollback();raise HTTPException(503,'Could not save collaboration changes. Refresh before retrying.') from None


async def json_body(request, keys):
    try:body=await request.json()
    except ValueError:raise HTTPException(422,'Provide valid JSON') from None
    if not isinstance(body,dict) or set(body)!=set(keys):raise HTTPException(422,'Invalid collaboration fields')
    return body


def build_router(get_db,require_user,csrf):
    router=APIRouter(prefix='/crm/whatsapp/api')
    base='/conversations/{cid}'

    @router.get(base+'/collaboration')
    def collaboration(cid:int,response:Response,db=Depends(get_db),user=Depends(require_user)):
        row=access.require_visible(db,user,cid);response.headers['Cache-Control']='private, no-store'
        shares=[];audit=[];candidates=[]
        if user.role=='ADMIN':
            candidates=[{'id':u.id,'username':u.username} for u in db.query(User).filter(User.role!='ADMIN').order_by(User.username) if u.id!=row.assigned_to_user_id and eligible(u)]
            shares=[{'user_id':s.user_id,'username':db.get(User,s.user_id).username,'can_reply':s.can_reply} for s in db.query(ConversationShare).filter_by(conversation_id=cid)]
            audit=[{'actor':db.get(User,a.actor_id).username,'user':db.get(User,a.user_id).username,'action':a.action,'can_reply':a.can_reply,'at':a.created_at.isoformat()+'Z'} for a in db.query(ConversationShareAudit).filter_by(conversation_id=cid).order_by(ConversationShareAudit.id.desc()).limit(30)]
        return {'version':row.assignment_version,'shares':shares,'audit':audit,'share_candidates':candidates,**access.permissions(db,user,row),
                'participants':[{'id':u.id,'username':u.username} for u in participants(db,row)]}

    @router.post(base+'/sharing')
    async def sharing(cid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        if user.role!='ADMIN':raise HTTPException(403,'Only administrators can share conversations')
        csrf(request);body=await json_body(request,{'user_id','can_reply','revoke','expected_version'})
        if type(body['user_id']) is not int or type(body['can_reply']) is not bool or type(body['revoke']) is not bool or type(body['expected_version']) is not int or body['expected_version']<0:
            raise HTTPException(422,'Invalid sharing permission or version')
        row=access.require_visible(db,user,cid)
        target=db.get(User,body['user_id'])
        if not target or (not body['revoke'] and (not eligible(target) or target.role=='ADMIN' or target.id==row.assigned_to_user_id)):
            raise HTTPException(422,'Choose an active staff member with Inbox access who is not the owner')
        changed=db.query(WhatsAppConversation).filter_by(id=cid,assignment_version=body['expected_version']).update(
            {'assignment_version':body['expected_version']+1},synchronize_session=False)
        if not changed:db.rollback();raise HTTPException(409,'Permissions changed. Refresh and try again')
        share=db.query(ConversationShare).filter_by(conversation_id=cid,user_id=target.id).one_or_none()
        if body['revoke']:
            if share:db.delete(share)
            action='REVOKED'
        else:
            action='UPDATED' if share else 'GRANTED'
            if share:share.can_reply=body['can_reply'];share.granted_by=user.id
            else:db.add(ConversationShare(conversation_id=cid,user_id=target.id,can_reply=body['can_reply'],granted_by=user.id))
        db.add(ConversationShareAudit(conversation_id=cid,actor_id=user.id,user_id=target.id,action=action,can_reply=body['can_reply'] if not body['revoke'] else False))
        commit(db)
        return {'ok':True,'version':body['expected_version']+1}

    @router.get(base+'/notes')
    def notes(cid:int,response:Response,before_id:int|None=None,db=Depends(get_db),user=Depends(require_user)):
        access.require_visible(db,user,cid);response.headers['Cache-Control']='private, no-store'
        query=db.query(ConversationNote).filter_by(conversation_id=cid)
        if before_id is not None:query=query.filter(ConversationNote.id<before_id)
        rows=query.order_by(ConversationNote.id.desc()).limit(51).all()
        return {'notes':[note_data(db,n) for n in reversed(rows[:50])],'next_before':rows[49].id if len(rows)>50 else None}

    @router.post(base+'/notes')
    async def add_note(cid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        csrf(request);access.require_visible(db,user,cid)
        body=await json_body(request,{'body','mentions','client_key'})
        if not isinstance(body['body'],str) or not body['body'].strip() or len(body['body'])>4000 or not isinstance(body['client_key'],str) or not 8<=len(body['client_key'])<=80 or not isinstance(body['mentions'],list) or len(body['mentions'])>10 or any(type(uid) is not int for uid in body['mentions']):
            raise HTTPException(422,'Provide a note up to 4000 characters and up to 10 mentions')
        text=body['body'].strip();ids=set(body['mentions'])
        def existing():
            prior=db.query(ConversationNote).filter_by(conversation_id=cid,author_id=user.id,client_key=body['client_key']).one_or_none()
            if prior:
                saved={m.user_id for m in db.query(ConversationMention).filter_by(note_id=prior.id)}
                if prior.body!=text or saved!=ids:raise HTTPException(409,'This note key was already used for different content')
                return note_data(db,prior)
        prior=existing()
        if prior:return prior
        row=access.require_visible(db,user,cid)
        if not ids.issubset({p.id for p in participants(db,row)}):raise HTTPException(422,'Mention only staff who already have access. Ask an administrator to share first.')
        note=ConversationNote(conversation_id=cid,author_id=user.id,client_key=body['client_key'],body=text)
        try:
            db.add(note);db.flush()
            for uid in ids:db.add(ConversationMention(note_id=note.id,user_id=uid))
            # Recheck permissions before committing; no outbound message or lead mutation.
            access.require_visible(db,user,cid);db.commit()
        except IntegrityError:
            db.rollback();prior=existing()
            if prior:return prior
            raise HTTPException(409,'Note changed. Refresh before retrying') from None
        except SQLAlchemyError:
            db.rollback();raise HTTPException(503,'Note could not be saved') from None
        return note_data(db,note)

    @router.get('/mentions')
    def notifications(response:Response,db=Depends(get_db),user=Depends(require_user)):
        response.headers['Cache-Control']='private, no-store'
        query=access.scope(db.query(ConversationMention,ConversationNote,WhatsAppConversation).join(
            ConversationNote,ConversationMention.note_id==ConversationNote.id).join(
            WhatsAppConversation,ConversationNote.conversation_id==WhatsAppConversation.id),user).filter(
            ConversationMention.user_id==user.id,ConversationMention.read_at.is_(None))
        count=query.count()
        rows=query.order_by(ConversationMention.id.desc()).limit(50).all()
        return {'unread':count,'mentions':[{'id':m.id,'conversation_id':c.id,'customer_name':c.customer_name or 'WhatsApp customer',
            'note_id':n.id,'author':db.get(User,n.author_id).username,'preview':n.body[:120]} for m,n,c in rows]}

    @router.post('/mentions/{mention_id}/read')
    def read_notification(mention_id:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        csrf(request)
        mention=db.query(ConversationMention).filter_by(id=mention_id,user_id=user.id).one_or_none()
        if not mention:raise HTTPException(404,'Mention not found')
        note=db.get(ConversationNote,mention.note_id);access.require_visible(db,user,note.conversation_id)
        mention.read_at=datetime.utcnow();commit(db);return {'ok':True}

    return router
