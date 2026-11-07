"""Authenticated, scoped Phase 1 consent UI/API. No marketing transmission API."""
import asyncio
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from datetime import datetime,timedelta
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from sqlalchemy import or_,cast,String,func
from sqlalchemy.exc import SQLAlchemyError,IntegrityError
from models import MarketingConsent as Consent,MarketingConsentEvent as Event,MarketingConsentRequest as ConsentRequest,WhatsAppConversation,User
import marketing_consent_service as service
import conversation_access
from quick_message_service import authorize
from marketing_consent_constants import STATUSES,SOURCES,INTERESTS,WORDING


def scoped(query,db,user):
    if user.role=='ADMIN':return query
    return query.filter(Consent.conversation_id.in_(conversation_access.scope(db.query(WhatsAppConversation.id),user)))


def authorized_contact(db,user,cid):
    row=scoped(db.query(Consent),db,user).filter(Consent.id==cid).populate_existing().one_or_none()
    if not row:raise HTTPException(404,'Consent contact not found or no longer accessible.')
    return row


async def body(request):
    raw=bytearray()
    try:
        async with asyncio.timeout(10):
            async for part in request.stream():
                raw.extend(part)
                if len(raw)>16384:raise HTTPException(413,'Consent action is too large.')
    except TimeoutError:raise HTTPException(408,'Consent action upload timed out.') from None
    import json
    try:
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError()
        return value
    except (ValueError,UnicodeError):raise HTTPException(422,'Provide valid consent action JSON.') from None


def commit(db):
    try:db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,'Consent changed concurrently. Refresh before retrying.') from None
    except SQLAlchemyError:db.rollback();raise HTTPException(503,'Consent changes could not be saved. No automatic retry was made.') from None


class ConsentRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def safe(request):
            try:return await original(request)
            except SQLAlchemyError:
                return JSONResponse({'detail':'Consent storage is unavailable. Refresh before retrying.'},status_code=503,headers={'Cache-Control':'private, no-store'})
        return safe


def build_router(get_db,require_user,csrf,csrf_token,templates):
    router=APIRouter(route_class=ConsentRoute)

    @router.get('/crm/marketing-consent')
    def dashboard(request:Request,db=Depends(get_db),user=Depends(require_user)):
        return templates.TemplateResponse('marketing_consent.html',{'request':request,'user':user,'page_title':'Marketing Consent',
            'consent_csrf':csrf_token(request),'consent_statuses':STATUSES,'consent_sources':SOURCES,'consent_interests':INTERESTS},headers={'Cache-Control':'private, no-store'})

    @router.get('/crm/marketing-consent/api')
    def listing(response:Response,q:str='',status:str='',source:str='',interest:str='',date_from:str='',date_to:str='',offset:int=0,include_archived:bool=False,db=Depends(get_db),user=Depends(require_user)):
        response.headers['Cache-Control']='private, no-store'
        if offset<0 or len(q)>100:raise HTTPException(422,'Invalid search or offset.')
        base=scoped(db.query(Consent),db,user)
        if include_archived and user.role!='ADMIN':raise HTTPException(403,'Archived identities are administrator-only.')
        if not include_archived:base=base.filter(or_(Consent.customer_id.isnot(None),Consent.conversation_id.isnot(None),Consent.lead_id.isnot(None)))
        query=base
        if status:
            if status not in STATUSES:raise HTTPException(422,'Choose an existing consent status.')
            query=query.filter(Consent.status==status)
        if source:
            if source not in SOURCES:raise HTTPException(422,'Choose an existing consent source.')
            query=query.filter(Consent.source==source)
        if interest:
            if interest not in INTERESTS:raise HTTPException(422,'Choose an existing product interest.')
            query=query.filter(cast(Consent.interests,String).like('%"'+interest+'"%'))
        if q.strip():query=query.filter(or_(Consent.display_name.ilike('%'+q.strip()+'%'),Consent.phone_e164.contains(q.strip())))
        try:
            start=datetime.strptime(date_from,'%Y-%m-%d')-timedelta(hours=5,minutes=30) if date_from else None
            end=datetime.strptime(date_to,'%Y-%m-%d')+timedelta(days=1)-timedelta(hours=5,minutes=30) if date_to else None
            if start and end and start>=end:raise ValueError()
        except ValueError:raise HTTPException(422,'Use a valid date range (Sri Lanka dates).') from None
        if start:query=query.filter(Consent.updated_at>=start)
        if end:query=query.filter(Consent.updated_at<end)
        counts={state:base.filter(Consent.status==state).count() for state in STATUSES}
        eligible=sum(service.eligibility(db,row)['eligible'] for row in base.filter(Consent.status=='OPTED_IN').yield_per(200))
        return {'contacts':[service.public(db,r) for r in query.order_by(Consent.updated_at.desc(),Consent.id.desc()).offset(offset).limit(50)],
                'total':query.count(),'offset':offset,'kpis':{'total':sum(counts.values()),**counts,'eligible':eligible}}

    @router.get('/crm/whatsapp/api/conversations/{conversation_id}/marketing-consent')
    def inbox_contact(conversation_id:int,response:Response,db=Depends(get_db),user=Depends(require_user)):
        conversation=conversation_access.require_visible(db,user,conversation_id)
        row=service.contact_for_conversation(db,conversation)
        if not row:raise HTTPException(409,'Consent identity is not initialized. Verify the consent migration.')
        authorized_contact(db,user,row.id)
        response.headers['Cache-Control']='private, no-store'
        window=True;explanation=None
        try:service.require_reply_window(db,conversation)
        except HTTPException as error:window=False;explanation=error.detail
        unresolved=db.query(ConsentRequest.id).filter(ConsentRequest.consent_id==row.id,ConsentRequest.status.in_(['SENDING','UNKNOWN'])).first()
        return {'contact':service.public(db,row,user.role=='ADMIN'),'can_verify':user.role=='ADMIN',
                'can_request':not unresolved and conversation_access.permissions(db,user,conversation)['can_reply'] and window and row.status=='NOT_REQUESTED' and service.valid_identity(db,row),
                'request_explanation':explanation or ('An earlier request outcome is uncertain; administrator review is required.' if unresolved else None) or ('A pending/granted/withdrawn consent or identity conflict blocks new requests.' if row.status!='NOT_REQUESTED' or not service.valid_identity(db,row) else None),
                'interests':INTERESTS,'wording':WORDING}

    @router.get('/crm/marketing-consent/api/{contact_id}')
    @router.get('/crm/whatsapp/api/marketing-consent/{contact_id}')
    def detail(contact_id:int,response:Response,db=Depends(get_db),user=Depends(require_user)):
        row=authorized_contact(db,user,contact_id);response.headers['Cache-Control']='private, no-store'
        return {'contact':service.public(db,row,user.role=='ADMIN'),'can_verify':user.role=='ADMIN','interests':INTERESTS,'wording':WORDING}

    @router.get('/crm/marketing-consent/api/{contact_id}/history')
    @router.get('/crm/whatsapp/api/marketing-consent/{contact_id}/history')
    def history(contact_id:int,response:Response,before_id:int|None=None,db=Depends(get_db),user=Depends(require_user)):
        authorized_contact(db,user,contact_id);response.headers['Cache-Control']='private, no-store'
        query=db.query(Event).filter_by(consent_id=contact_id)
        if before_id:query=query.filter(Event.id<before_id)
        rows=query.order_by(Event.id.desc()).limit(51).all();events=[]
        for row in rows[:50]:
            item={'id':row.id,'type':row.event_type,'previous':row.previous_status,'status':row.new_status,'actor':row.actor_name,'at':service.stamp(row.recorded_at)}
            if user.role=='ADMIN':item.update(evidence_reference=row.evidence_reference,evidence_type=row.evidence_type,wording=row.wording,reason=row.reason,source=row.source,evidence_at=service.stamp(row.evidence_at),source_description=row.source_description,evidence_text=row.evidence_text)
            events.append(item)
        return {'events':events,'next_before':rows[49].id if len(rows)>50 else None}

    @router.post('/crm/marketing-consent/api/{contact_id}/action')
    @router.post('/crm/whatsapp/api/marketing-consent/{contact_id}/action')
    async def action(contact_id:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        csrf(request);authorize(user);row=authorized_contact(db,user,contact_id)
        service.apply_action(db,row,user,await body(request));commit(db)
        return {'contact':service.public(db,row,user.role=='ADMIN')}

    @router.post('/crm/whatsapp/api/conversations/{conversation_id}/marketing-consent/request')
    async def prepare(conversation_id:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        csrf(request);authorize(user);conversation=conversation_access.require_owned(db,user,conversation_id)
        row=service.contact_for_conversation(db,conversation)
        if not row:raise HTTPException(409,'Consent identity is not initialized.')
        data=await body(request)
        result=service.prepare_request(db,row,conversation,user,data.get('language'),data.get('key'))
        commit(db)
        return {'request_id':result.id,'text':result.message_text,'key':result.idempotency_key,'status':result.status,'version':result.wording_version}

    return router
