"""Private, CSRF-protected workload actions; no send operations."""
import json
from fastapi import APIRouter,Depends,Request,HTTPException,Query
from sqlalchemy.exc import IntegrityError
from models import ConversationWorkEvent as Event
import conversation_access
import conversation_work_service as work
from crm_dashboard_routes import DashboardRoute


def build_router(get_db,require_user,csrf,csrf_token):
    router=APIRouter(route_class=DashboardRoute)
    async def body(request):
        csrf(request);raw=bytearray()
        async for chunk in request.stream():
            if len(raw)+len(chunk)>65536:raise HTTPException(413,'Status request is too large; select at most 100 conversations')
            raw.extend(chunk)
        try:return json.loads(raw)
        except (ValueError,UnicodeError):raise HTTPException(422,'Invalid status request') from None

    @router.get('/crm/whatsapp/api/work-csrf')
    def token(request:Request,user=Depends(require_user)):
        return {'csrf_token':csrf_token(request),'admin':user.role=='ADMIN','user_id':user.id}

    @router.get('/crm/whatsapp/api/conversations/{cid}/work-status')
    def current(cid:int,db=Depends(get_db),user=Depends(require_user)):
        c=conversation_access.require_visible(db,user,cid)
        return {'id':cid,'name':c.customer_name or 'WhatsApp customer','work':work.snapshots(db,[c])[cid],
            **conversation_access.permissions(db,user,c)}

    @router.post('/crm/whatsapp/api/conversations/{cid}/work-status')
    async def change(cid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        payload=await body(request)
        try:result=work.transition(db,user,cid,payload);db.commit();return {'work':result}
        except IntegrityError:db.rollback();raise HTTPException(409,'Conversation changed. Refresh and review.') from None

    @router.get('/crm/whatsapp/api/conversations/{cid}/work-history')
    def history(cid:int,offset:int=Query(0,ge=0,le=100000),db=Depends(get_db),user=Depends(require_user)):
        conversation_access.require_visible(db,user,cid)
        q=db.query(Event).filter_by(conversation_id=cid)
        return {'total':q.count(),'events':[{'previous_status':e.previous_status,'status':e.new_status,'actor':e.actor_name or 'System',
            'at':work.iso(e.created_at),'reason':e.reason,'trigger':e.trigger,'inbound_id':e.inbound_id,
            'due_at':work.iso(e.due_at),'followup_user_id':e.followup_user_id} for e in q.order_by(Event.id.desc()).offset(offset).limit(50)]}

    @router.post('/crm/whatsapp/api/work-status/bulk')
    async def bulk(request:Request,db=Depends(get_db),user=Depends(require_user)):
        if user.role!='ADMIN':raise HTTPException(403,'Only administrators can bulk resolve')
        payload=await body(request)
        if not isinstance(payload,dict):raise HTTPException(422,'Invalid selection')
        items=payload.get('items');reason=payload.get('reason','');key=payload.get('key')
        if not isinstance(items,list) or not 1<=len(items)<=100:raise HTTPException(422,'Select 1–100 explicit conversations')
        if any(not isinstance(i,dict) or type(i.get('id')) is not int for i in items) or len({i['id'] for i in items})!=len(items):raise HTTPException(422,'Select each conversation only once')
        # SQLite's legacy driver does not start a transaction for SELECT. Start
        # the write transaction before savepoints so releasing one cannot commit it.
        connection=db.connection()
        if connection.dialect.name=='sqlite' and not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        results=[]
        for item in sorted(items,key=lambda i:i['id']):
            try:
                with db.begin_nested():
                    result=work.transition(db,user,item['id'],{'action':'resolve','key':key,'reason':reason,
                        'version':item.get('version'),'last_inbound_id':item.get('last_inbound_id'),'review_token':item.get('review_token')})
                results.append({'id':item['id'],'ok':True,'work':result})
            except HTTPException as e:results.append({'id':item['id'],'ok':False,'status':e.status_code,'error':e.detail})
            except IntegrityError:results.append({'id':item['id'],'ok':False,'status':409,'error':'Conversation changed. Refresh and review.'})
        by_id={r['id']:r for r in results}
        db.commit();return {'results':[by_id[i['id']] for i in items]}
    return router
