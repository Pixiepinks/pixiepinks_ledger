from uuid import uuid4
from datetime import datetime
import csv,io
from fastapi import APIRouter,Depends,Request,HTTPException,Query
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from crm_dashboard_routes import DashboardRoute
import sales_pipeline as s
from sales_pipeline_models import SalesOpportunity as O,SalesPipelineEvent as E,SalesPipelineControl as Control
from models import Lead,User,WhatsAppConversation as C
from learning_routes import read_body
import conversation_access as access

def build_router(get_db,require_user,csrf,token,templates):
    r=APIRouter(route_class=DashboardRoute)
    @r.get('/crm/sales-pipeline')
    def page(request:Request,user=Depends(require_user)):
        s.authorize(user)
        return templates.TemplateResponse('sales_pipeline.html',{'request':request,'user':user,'page_title':'WhatsApp Sales Pipeline','csrf_token':token(request)})
    @r.get('/crm/sales-pipeline/api')
    def listing(request:Request,db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user)
        try:
            data=s.report(db,user,dict(request.query_params))
            data['staff']=[{'id':v.id,'name':v.username} for v in db.query(User).filter(User.is_active.is_(True),User.role.in_(['ADMIN','STAFF','SALES_AGENT','SALES_MANAGER']))] if user.role=='ADMIN' else []
            return data
        except (ValueError,TypeError):raise HTTPException(422,'Invalid filter') from None
    @r.get('/crm/sales-pipeline/api/{oid:int}')
    def detail(oid:int,db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user);o=s.scope(db,user).filter(O.id==oid).one_or_none()
        if not o:raise HTTPException(404,'Opportunity not found')
        c=access.require_visible(db,user,o.conversation_id)
        return {'opportunity':s.batch(db,[o])[0],'can_edit':access.permissions(db,user,c)['can_manage'] and o.active_conversation_id is not None,'history':[{'previous':v.previous,'current':v.current,'actor_id':v.actor_id,'at':s.stamp(v.created_at)} for v in db.query(E).filter_by(opportunity_id=oid).order_by(E.id.desc()).limit(50)]}
    @r.post('/crm/sales-pipeline/api/{oid:int}')
    async def edit(oid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user);csrf(request);p=await read_body(request)
        try:
            o=s.update(db,user,oid,p,request.headers.get('Idempotency-Key',''));db.commit();return {'opportunity':s.batch(db,[o])[0]}
        except (HTTPException,IntegrityError) as error:
            db.rollback()
            if isinstance(error,HTTPException):raise
            raise HTTPException(409,'Concurrent edit; refresh and review') from None
    @r.post('/crm/sales-pipeline/api/conversations/{cid}/new')
    async def create(cid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user);csrf(request);p=await read_body(request)
        c=access.require_visible(db,user,cid);s.manageable(db,user,c)
        db.query(C).filter_by(id=cid).with_for_update().one()
        current=db.query(O).filter_by(active_conversation_id=cid).one_or_none()
        if current:
            if s.stage(current,db.get(Lead,current.lead_id)) not in {'CLOSED','CONFIRMED'}:return {'opportunity':s.batch(db,[current])[0]}
            if not p.get('new_intent'):return {'opportunity':s.batch(db,[current])[0]}
            previous=dict(current.data);current.data={**previous,'archived_lead_status':db.get(Lead,current.lead_id).status}
            db.add(E(opportunity_id=current.id,actor_id=user.id,previous=previous,current={**current.data,'archived_for_new_intent':True},key='archive:'+str(uuid4())))
            current.active_conversation_id=None;db.flush()
        lead=db.query(Lead).filter_by(whatsapp_conversation_id=cid).one_or_none()
        if not lead:raise HTTPException(409,'Classify/link the inquiry in the existing Inbox first; no customer registration required')
        lead.status='NEW';o=s.ensure(db,lead,user.id,new_intent=True);db.commit();return {'opportunity':s.batch(db,[o])[0]}
    @r.get('/crm/sales-pipeline/backfill')
    def backfill(db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user)
        if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
        return s.preview(db)
    @r.post('/crm/sales-pipeline/backfill')
    async def apply(request:Request,db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user);csrf(request)
        if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
        p=await read_body(request);v=s.preview(db)
        if p.get('confirmation')!='APPLY PIPELINE BACKFILL' or p.get('digest')!=v['digest']:raise HTTPException(409,'Fresh preview digest and explicit approval required')
        created=0
        for item in v['proposed']:
            db.query(C).filter_by(id=item['conversation_id']).with_for_update().one()
            if db.query(O).filter_by(active_conversation_id=item['conversation_id']).first():continue
            lead=db.query(Lead).filter_by(id=item['lead_id']).populate_existing().with_for_update().one()
            import hashlib,json
            actual=hashlib.sha256(json.dumps([lead.status,lead.product_interest,lead.notes,s.stamp(lead.next_followup)],default=str).encode()).hexdigest()
            if actual!=item['fingerprint']:raise HTTPException(409,'Backfill evidence changed; preview again')
            o=s.ensure(db,lead,user.id);created+=1;previous=dict(o.data);o.data={**previous,'backfill_digest':v['digest']}
            db.add(E(opportunity_id=o.id,actor_id=user.id,previous=previous,current=o.data,key='backfill:'+str(uuid4())))
        db.commit();return {'status':'APPLIED','created':created}
    @r.get('/crm/sales-pipeline/export')
    def export(request:Request,db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user)
        if user.role not in {'ADMIN','SALES_MANAGER'}:raise HTTPException(403,'Manager reporting required')
        out=io.StringIO();w=csv.writer(out);w.writerow(['Stage','Customer','Product','Expected payment','Expected purchase','Outstanding LKR'])
        export_rows=s.report(db,user,dict(request.query_params),export=True)
        for v in export_rows:w.writerow([("'"+str(x)) if str(x).lstrip().startswith(('=','+','-','@')) or str(x).startswith(('\t','\r','\n')) else x for x in [v['label'],v['name'],v.get('product',''),v.get('payment_date'),v.get('purchase_date'),v.get('outstanding')]])
        return Response(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=pipeline.csv'})
    @r.get('/crm/sales-pipeline/settings')
    def control(db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user)
        if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
        return s.config(db)
    @r.post('/crm/sales-pipeline/settings')
    async def configure(request:Request,db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user);csrf(request)
        if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
        p=await read_body(request)
        if set(p)-set(s.DEFAULTS) or any(type(p.get(k)) is not int or not 0<=p[k]<=maxi for k,maxi in [('hour',23),('minute',59)]):raise HTTPException(422,'Invalid reminder timing')
        for key in ['payment_offsets','purchase_offsets']:
            if not isinstance(p.get(key),list) or len(p[key])>10 or any(type(v) is not int or not -30<=v<=30 for v in p[key]) or len(set(p[key]))!=len(p[key]):raise HTTPException(422,'Invalid reminder offsets')
        row=db.get(Control,1)
        if not row:row=Control(id=1);db.add(row)
        previous=dict(row.data or {})
        row.data=p;db.flush();db.add(E(opportunity_id=None,actor_id=user.id,previous={'settings':previous},current={'settings':p},key='settings:'+str(uuid4())))
        for o in db.query(O).filter(O.active_conversation_id.isnot(None)):o.version+=1;s.schedule(db,o,db.get(C,o.conversation_id))
        db.commit();return p
    @r.post('/crm/sales-pipeline/api/{oid:int}/associate-order')
    async def associate(oid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user);csrf(request);p=await read_body(request)
        o=s.scope(db,user).filter(O.id==oid).with_for_update().one_or_none()
        if not o:raise HTTPException(404,'Opportunity not found')
        c=access.require_visible(db,user,o.conversation_id);s.manageable(db,user,c)
        from models import SalesOrder
        order=db.get(SalesOrder,p.get('order_id')) if type(p.get('order_id')) is int else None
        if not order or order.crm_conversation_id!=c.id:raise HTTPException(409,'Order/conversation identity mismatch')
        if o.data.get('order_id')==order.id:return {'linked':True,'stage':s.stage(o,db.get(Lead,o.lead_id))}
        if o.data.get('order_id'):raise HTTPException(409,'Opportunity already has an order; never create another to recover')
        if p.get('version')!=o.version:raise HTTPException(409,'Opportunity changed; link existing order after review')
        old=dict(o.data);o.data={**old,'order_id':order.id};o.order_id=order.id;o.version+=1
        db.add(E(opportunity_id=o.id,actor_id=user.id,previous=old,current=o.data,key='order:'+str(uuid4())))
        if s.convertible(order):
            db.get(Lead,o.lead_id).status='ORDER_CONFIRMED'
        s.schedule(db,o,c);db.commit();return {'linked':True,'stage':s.stage(o,db.get(Lead,o.lead_id))}
    @r.post('/crm/sales-pipeline/api/{oid:int}/assign')
    async def assign(oid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user);csrf(request);p=await read_body(request)
        if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
        o=s.scope(db,user).filter(O.id==oid).one_or_none()
        if not o:raise HTTPException(404,'Opportunity not found')
        if p.get('owner_id') is not None and type(p['owner_id']) is not int:raise HTTPException(422,'Choose a staff account')
        if type(p.get('assignment_version')) is not int:raise HTTPException(422,'Assignment version required')
        c=access.assign(db,user,o.conversation_id,p.get('owner_id'),p.get('assignment_version'))
        return {'assigned_id':c.assigned_to_user_id}
    @r.get('/crm/sales-pipeline/backfill/rollback')
    def rollback_preview(digest:str,db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user)
        if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
        rows=db.query(O).filter(O.data['backfill_digest'].as_string()==digest,O.active_conversation_id.isnot(None)).all()
        return {'digest':digest,'unchanged_ids':[o.id for o in rows if o.version==0 and not o.order_id],'needs_review':[o.id for o in rows if o.version!=0 or o.order_id]}
    @r.post('/crm/sales-pipeline/backfill/rollback')
    async def rollback_apply(request:Request,db=Depends(get_db),user=Depends(require_user)):
        s.authorize(user);csrf(request)
        if user.role!='ADMIN':raise HTTPException(403,'Administrator required')
        p=await read_body(request)
        if p.get('confirmation')!='ARCHIVE UNCHANGED BACKFILL' or not isinstance(p.get('digest'),str):raise HTTPException(422,'Explicit reviewed backfill digest required')
        rows=db.query(O).filter(O.data['backfill_digest'].as_string()==p['digest'],O.active_conversation_id.isnot(None)).with_for_update().all()
        if any(o.version!=0 or o.order_id for o in rows):raise HTTPException(409,'Edited or linked opportunities require manual review; nothing archived')
        for o in rows:
            previous=dict(o.data);o.active_conversation_id=None;o.data={**previous,'stage':'CLOSED','closed_reason':'Backfill rollback; original Inbox status preserved'};o.version+=1
            db.add(E(opportunity_id=o.id,actor_id=user.id,previous=previous,current=o.data,key='rollback:'+str(uuid4())))
            s.schedule(db,o,db.get(C,o.conversation_id))
        db.commit();return {'archived':len(rows),'inbox_statuses_modified':False}
    return r
