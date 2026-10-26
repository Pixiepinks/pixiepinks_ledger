"""Authenticated contact pipeline using real conversations and message-scoped referrals."""
from datetime import date,datetime,timezone

from fastapi import APIRouter,Depends,HTTPException,Query,Request,Response
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from sqlalchemy import case,func,or_
from sqlalchemy.exc import SQLAlchemyError
from models import Lead,LeadNote,CRMUser,WhatsAppConversation,WhatsAppConversationMessage as Message,WhatsAppMessageReferral as Referral
from referral_preview_service import preview_candidate
from whatsapp_referral_service import public_referral,extract_referral

class LeadRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handler(request):
            try:
                response=await original(request);response.headers['Cache-Control']='private, no-store'
                response.headers['X-Content-Type-Options']='nosniff';return response
            except SQLAlchemyError:return JSONResponse({'detail':'CRM leads temporarily unavailable'},status_code=503,headers={'Cache-Control':'private, no-store'})
        return handler


ROLES={'ADMIN','SALES_MANAGER','SALES_AGENT','STAFF'}


def authorize(user):
    if user.role not in ROLES:raise HTTPException(403,'Staff access required')


def iso(value):return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def lead_data(lead):
    return {'id':lead.id,'lead_no':lead.lead_no,'status':lead.status,'assigned_to':lead.assigned_to,
        'product_interest':lead.product_interest,'next_followup':str(lead.next_followup) if lead.next_followup else None,
        'url':f'/crm/leads/{lead.id}'} if lead else None


def referral_data(row,conversation_id,preview_tag):
    safe=public_referral(row) or {}
    # Advertisement IDs are attribution only, never catalogue/product identifiers.
    identity=extract_referral({'referral':{'source_id':row.get('source_id')}}) or {}
    safe.update({'source_id':identity.get('source_id'),'message_id':row['message_id'],'captured_at':iso(row['captured_at'])})
    candidate=preview_candidate(row)
    if candidate:
        url,kind=candidate
        safe['preview_url']=(f'/crm/whatsapp/api/conversations/{conversation_id}/messages/{row["message_id"]}'
            f'/referrals/{row["id"]}/preview?v={preview_tag(url)}')
        safe['preview_kind']=kind
    return safe


def referral_columns():
    # Deliberately exclude ctwa_clid, video URLs and complete webhook payloads.
    return [Referral.id,Referral.message_id,Referral.source_id,Referral.source_type,Referral.headline,Referral.body,
        Referral.source_url,Referral.media_type,Referral.image_url,Referral.thumbnail_url,Referral.captured_at]


def listing(db,preview_tag,search='',source='',status='',assigned='',offset=0):
    referral_query=db.query(Referral.id).join(Message,Message.id==Referral.message_id).filter(
        Message.phone_number==WhatsAppConversation.phone_number,Message.direction=='inbound')
    query=db.query(WhatsAppConversation,Lead).outerjoin(Lead,Lead.whatsapp_conversation_id==WhatsAppConversation.id)
    if search:
        pattern='%'+search+'%'
        query=query.filter(or_(WhatsAppConversation.customer_name.ilike(pattern),WhatsAppConversation.phone_number.ilike(pattern),
            Lead.lead_no.ilike(pattern),Lead.product_interest.ilike(pattern)))
    if source=='ad':query=query.filter(referral_query.filter(func.lower(Referral.source_type)=='ad').exists())
    elif source=='post':query=query.filter(referral_query.filter(func.lower(Referral.source_type)=='post').exists())
    elif source=='none':query=query.filter(~referral_query.exists())
    elif source=='referral':query=query.filter(referral_query.exists())
    if status=='UNORGANIZED':query=query.filter(Lead.id.is_(None))
    elif status:query=query.filter(Lead.status==status)
    if assigned=='UNASSIGNED':query=query.filter(or_(Lead.assigned_to.is_(None),Lead.assigned_to==''))
    elif assigned:query=query.filter(Lead.assigned_to==assigned)
    total=query.count();rows=query.order_by(WhatsAppConversation.last_message_at.desc(),WhatsAppConversation.id.desc()).offset(offset).limit(50).all()
    phones=[c.phone_number for c,_ in rows];referrals={}
    if phones:
        ranked=db.query(*referral_columns(),Message.phone_number.label('phone'),
            func.row_number().over(partition_by=Message.phone_number,order_by=(Referral.captured_at.desc(),Referral.id.desc())).label('rank'),
            func.count().over(partition_by=Message.phone_number).label('total'),
            func.sum(case((func.lower(Referral.source_type)=='ad',1),else_=0)).over(partition_by=Message.phone_number).label('ad_count')).join(
            Message,Message.id==Referral.message_id).filter(Message.phone_number.in_(phones),Message.direction=='inbound').subquery()
        for row in db.query(ranked).filter(ranked.c.rank==1):referrals[row.phone]=row._mapping
    items=[]
    for conversation,lead in rows:
        referral=referrals.get(conversation.phone_number)
        items.append({'conversation_id':conversation.id,'customer_name':conversation.customer_name or 'WhatsApp customer',
            'phone_number':conversation.phone_number,'mode':conversation.mode,'last_activity':iso(conversation.last_message_at),
            'inbox_url':f'/crm/whatsapp?conversation={conversation.id}','lead':lead_data(lead),
            'referral_count':referral['total'] if referral else 0,'ad_count':referral['ad_count'] if referral else 0,
            'latest_referral':referral_data(referral,conversation.id,preview_tag) if referral else None})
    return {'items':items,'total':total,'offset':offset}


def matching_leads(db,conversation,normalize_phone):
    # Candidate matching only; staff must explicitly choose which record to link.
    # No phone match is silently converted into a relationship.
    compact=normalize_phone(conversation.phone_number)
    values={conversation.phone_number,compact,'+'+compact}
    if compact.startswith('94') and len(compact)==11:values.add('0'+compact[2:])
    normalized=Lead.mobile
    for token in ['+',' ','-','(',')','\t','.']:normalized=func.replace(normalized,token,'')
    return db.query(Lead).filter(Lead.whatsapp_conversation_id.is_(None),or_(normalized.in_({normalize_phone(value) for value in values}|{value for value in values if value.startswith('0')}),
        Lead.notes.contains('\nWhatsApp: '+compact+'\n'),Lead.notes.startswith('WhatsApp: '+compact+'\n'))).order_by(Lead.id.desc()).limit(20).all()


def build_router(get_db,require_user,require_csrf,csrf_token,statuses,preview_tag,normalize_phone,status_labels=None):
    from learning_routes import read_body
    router=APIRouter(prefix='/crm/leads/api/whatsapp',route_class=LeadRoute)
    @router.get('')
    def get_listing(request:Request,response:Response,q:str=Query('',max_length=100),source:str=Query('',pattern='^(ad|post|none|referral)?$'),
        status:str=Query('',max_length=32),assigned:str=Query('',max_length=100),offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        authorize(user)
        if status and status not in statuses+['UNORGANIZED']:raise HTTPException(422,'Invalid status')
        return {**listing(db,preview_tag,q.strip(),source,status,assigned,offset),'statuses':statuses,'status_labels':status_labels or {},
            'assignees':[name for name, in db.query(CRMUser.name).order_by(CRMUser.name)],'csrf_token':csrf_token(request)}

    @router.get('/{cid}')
    def get_detail(cid:int,offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        authorize(user);conversation=db.get(WhatsAppConversation,cid)
        if not conversation:raise HTTPException(404,'Conversation not found')
        lead=db.query(Lead).filter_by(whatsapp_conversation_id=cid).one_or_none()
        rows=db.query(*referral_columns(),Message.message_text).join(Message,Message.id==Referral.message_id).filter(
            Message.phone_number==conversation.phone_number,Message.direction=='inbound').order_by(Referral.captured_at.desc(),Referral.id.desc())
        total=rows.count()
        referrals=[{**referral_data(row._mapping,cid,preview_tag),'customer_message':row.message_text[:300]} for row in rows.offset(offset).limit(20)]
        return {'conversation_id':cid,'customer_name':conversation.customer_name or 'WhatsApp customer','phone_number':conversation.phone_number,
            'lead':lead_data(lead),'referrals':referrals,'referral_total':total,'offset':offset,
            'matching_leads':[{'id':item.id,'lead_no':item.lead_no,'customer_name':item.customer_name} for item in matching_leads(db,conversation,normalize_phone)] if not lead else []}

    @router.post('/{cid}/lead')
    async def save_lead(cid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        authorize(user);require_csrf(request);body=await read_body(request)
        if set(body)-{'lead_id','status','assigned_to','product_interest','next_followup','note'}:raise HTTPException(422,'Unknown lead field')
        if 'lead_id' in body and (type(body['lead_id']) is not int or body['lead_id']<1):raise HTTPException(422,'Invalid lead ID')
        for key,limit in [('assigned_to',100),('product_interest',255),('note',2000),('next_followup',10)]:
            if key in body and (not isinstance(body[key],str) or len(body[key])>limit):raise HTTPException(422,'Invalid lead field')
        if 'status' in body and (not isinstance(body['status'],str) or body['status'] not in statuses):raise HTTPException(422,'Invalid status')
        try:followup=date.fromisoformat(body['next_followup']) if body.get('next_followup') else None
        except ValueError:raise HTTPException(422,'Invalid follow-up date') from None
        from learning_routes import mutate
        def operation():
            # A row lock serializes cross-session create/link/update on PostgreSQL.
            conversation=db.query(WhatsAppConversation).filter_by(id=cid).populate_existing().with_for_update().one_or_none()
            if not conversation:raise HTTPException(404,'Conversation not found')
            lead=db.query(Lead).filter_by(whatsapp_conversation_id=cid).one_or_none()
            if lead and body.get('lead_id') not in (None,lead.id):raise HTTPException(409,'Conversation already linked to a different lead')
            if lead is None and body.get('lead_id'):
                lead=db.query(Lead).filter_by(id=body['lead_id']).with_for_update().one_or_none()
                candidates={item.id for item in matching_leads(db,conversation,normalize_phone)}
                if lead is None or lead.whatsapp_conversation_id is not None or lead.id not in candidates:raise HTTPException(409,'Choose an available lead matching this WhatsApp number')
                # Manual linking is an explicit staff decision, not attribution inferred from a phone.
                lead.whatsapp_conversation_id=cid
            if lead is None:
                if matching_leads(db,conversation,normalize_phone):raise HTTPException(409,'Existing leads match this number; choose a record to link before creating another')
                lead=Lead(lead_no='LD-WA-'+str(cid),customer_name=conversation.customer_name or 'WhatsApp customer',mobile=conversation.phone_number,
                    source='WhatsApp',status='NEW',whatsapp_conversation_id=cid)
                db.add(lead);db.flush()
            if 'status' in body:lead.status=body['status']
            if 'assigned_to' in body:
                assignee=body['assigned_to'].strip()
                if assignee and assignee!=lead.assigned_to and not db.query(CRMUser.id).filter_by(name=assignee).first():raise HTTPException(422,'Choose an existing CRM assignee')
                lead.assigned_to=assignee or None
            if 'product_interest' in body:lead.product_interest=body['product_interest'].strip()
            if 'next_followup' in body:lead.next_followup=followup
            if body.get('note','').strip():db.add(LeadNote(lead_id=lead.id,note=body['note'].strip()))
            return {'lead':lead_data(lead),'conversation_id':cid}
        try:return mutate(db,user,request.headers.get('Idempotency-Key',''),request.url.path,body,operation)
        except HTTPException:db.rollback();raise
    return router
