"""Internal staff reminders. Never modifies WhatsApp read state or sends to Meta."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from models import User, WhatsAppConversation, ConversationManualUnread as Mark, ConversationManualUnreadAudit as Audit
import conversation_access
import staff_access

# Dark, fixed palette on white. Names accompany colour; colour alone conveys no identity.
PALETTE={'blue':'#1d4ed8','purple':'#7e22ce','orange':'#9a3412','green':'#15803d','pink':'#be185d'}


def default_colour(staff_id):return tuple(PALETTE)[(staff_id-1)%len(PALETTE)]


def snapshots(db,ids):
    result={cid:[] for cid in ids}
    if not ids:return result
    rows=db.query(Mark.conversation_id,Mark.staff_id,User.username,User.unread_indicator_colour).join(User,User.id==Mark.staff_id).filter(Mark.conversation_id.in_(ids)).order_by(Mark.created_at,Mark.staff_id)
    for cid,uid,name,colour in rows:
        result[cid].append({'staff_id':uid,'name':name,'colour':colour if colour in PALETTE else default_colour(uid)})
    return result


def change(db,user,cid,staff_id,mark):
    if not staff_access.allowed(user,'whatsapp'):raise HTTPException(403,'WhatsApp Inbox access required')
    conversation_access.require_visible(db,user,cid)
    if staff_id!=user.id and (mark or user.role!='ADMIN'):raise HTTPException(403,'You may clear only your own unread mark')
    # Serialize only reminder actions. PostgreSQL row lock + composite PK prevents duplicates.
    db.query(WhatsAppConversation).filter_by(id=cid).with_for_update().one()
    conversation_access.require_visible(db,user,cid)
    if mark:
        if db.bind.dialect.name=='postgresql':from sqlalchemy.dialects.postgresql import insert
        elif db.bind.dialect.name=='sqlite':from sqlalchemy.dialects.sqlite import insert
        else:raise HTTPException(503,'Unread reminders unavailable on this database')
        changed=db.execute(insert(Mark).values(conversation_id=cid,staff_id=staff_id).on_conflict_do_nothing(index_elements=['conversation_id','staff_id'])).rowcount==1
    else:changed=db.query(Mark).filter_by(conversation_id=cid,staff_id=staff_id).delete(synchronize_session=False)>0
    if changed:db.add(Audit(conversation_id=cid,actor_id=user.id,staff_id=staff_id,action='MARKED' if mark else 'CLEARED'))
    db.commit()
    return {'manual_unread':snapshots(db,[cid])[cid]}


def build_router(get_db,require_user,csrf):
    router=APIRouter()
    @router.post('/crm/whatsapp/api/conversations/{conversation_id}/manual-unread')
    def mark(conversation_id:int,request:Request,response:Response,db=Depends(get_db),user=Depends(require_user)):
        csrf(request);response.headers['Cache-Control']='private, no-store';return change(db,user,conversation_id,user.id,True)
    @router.delete('/crm/whatsapp/api/conversations/{conversation_id}/manual-unread/{staff_id}')
    def clear(conversation_id:int,staff_id:int,request:Request,response:Response,db=Depends(get_db),user=Depends(require_user)):
        csrf(request);response.headers['Cache-Control']='private, no-store';return change(db,user,conversation_id,staff_id,False)
    return router
