from fastapi import APIRouter,Depends,HTTPException,Request,Response
from sqlalchemy.exc import SQLAlchemyError
from models import User,ConversationAssignmentAudit
import conversation_access as access
from staff_access import allowed


def build_router(get_db,require_user,csrf):
    router=APIRouter()
    def admin(user):
        if user.role!='ADMIN':raise HTTPException(403,'Only administrators can assign conversations')

    @router.get('/crm/whatsapp/api/assignees')
    def assignees(response:Response,db=Depends(get_db),user=Depends(require_user)):
        admin(user);response.headers['Cache-Control']='private, no-store'
        users=db.query(User).filter(User.is_active.is_(True),User.role.in_(['ADMIN','STAFF','SALES_MANAGER','SALES_AGENT'])).order_by(User.username).all()
        return {'assignees':[{'id':row.id,'username':row.username} for row in users if allowed(row,'whatsapp')]}

    @router.post('/crm/whatsapp/api/conversations/{cid}/assignment')
    async def assign(cid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        admin(user);csrf(request)
        try:body=await request.json()
        except ValueError:raise HTTPException(422,'Provide assignment JSON') from None
        if not isinstance(body,dict) or set(body)!={'owner_id','expected_version'} or type(body['expected_version']) is not int or body['expected_version']<0 or (body['owner_id'] is not None and type(body['owner_id']) is not int):
            raise HTTPException(422,'Provide an owner ID or null and the current assignment version')
        try:row=access.assign(db,user,cid,body['owner_id'],body['expected_version'])
        except SQLAlchemyError:
            db.rollback();raise HTTPException(503,'Assignment could not be saved. No changes committed.') from None
        return {'id':row.id,'owner':access.owner_data(db,row),'assignment_version':row.assignment_version}

    @router.get('/crm/whatsapp/api/conversations/{cid}/assignment-history')
    def history(cid:int,response:Response,db=Depends(get_db),user=Depends(require_user)):
        admin(user);access.require_owned(db,user,cid);response.headers['Cache-Control']='private, no-store'
        rows=db.query(ConversationAssignmentAudit).filter_by(conversation_id=cid).order_by(ConversationAssignmentAudit.id.desc()).limit(50).all()
        return {'history':[{'actor_id':r.actor_id,'previous_owner_id':r.previous_owner_id,'new_owner_id':r.new_owner_id,'at':r.created_at.isoformat()+'Z'} for r in rows]}
    return router
