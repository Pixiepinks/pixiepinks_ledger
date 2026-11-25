"""Bounded customer lookup/registration using existing channel permissions."""
from fastapi import APIRouter,Depends,Request,HTTPException,Query
from fastapi.responses import JSONResponse
import staff_access
import customer_registration as service
from models import Customer

def build_router(get_db,require_user,csrf):
    router=APIRouter()
    def access(user,channel):
        if channel=='POS':
            import pos_service
            pos_service.require(user,'sell')
        elif channel=='CRM':
            import main
            main._require_sales_orders_enabled()
            if not staff_access.allowed(user,'sales_orders') or not {'create','edit'}&staff_access.sales_permissions(user,main.SALES_ORDER_PERMISSIONS):raise HTTPException(403,'CRM order editing access required')
        elif channel=='CUSTOMER_MANAGEMENT':
            if not staff_access.allowed(user,'customers'):raise HTTPException(403,'Customer management permission required')
        else:raise HTTPException(422,'Choose CRM, POS or CUSTOMER_MANAGEMENT')
    def response(value):return JSONResponse(value,headers={'Cache-Control':'private, no-store'})
    @router.get('/api/customer-registration/search')
    def search(q:str=Query(...,min_length=1,max_length=100),channel:str='CRM',db=Depends(get_db),user=Depends(require_user)):
        access(user,channel);return response(service.search(db,q))
    @router.get('/api/customer-registration/profile/{cid}')
    def profile(cid:int,channel:str='CRM',db=Depends(get_db),user=Depends(require_user)):
        access(user,channel)
        if not 0<cid<=2147483647:raise HTTPException(422,'Invalid Customer ID')
        c=db.get(Customer,cid)
        if not c:raise HTTPException(404,'Customer not found')
        return response({**service.describe(db,c),'fingerprint':__import__('loyalty_service').fingerprint(c),**({'email':c.email} if channel=='CUSTOMER_MANAGEMENT' else {})})
    @router.get('/api/customer-registration/profile/{cid}/history')
    def history(cid:int,db=Depends(get_db),user=Depends(require_user)):
        if not staff_access.allowed(user,'customers') and not staff_access.allowed(user,'sales_orders'):raise HTTPException(403,'Customer/order history permission required')
        if not 0<cid<=2147483647 or not db.get(Customer,cid):raise HTTPException(404,'Customer not found')
        from models import SalesOrder
        import loyalty_service as loyalty
        mappings=[m.order_id for m in db.query(loyalty.LoyaltyOrderIdentity).filter_by(customer_id=cid)]
        rows=db.query(SalesOrder).filter((SalesOrder.customer_id==cid)|(SalesOrder.id.in_(mappings or [-1]))).order_by(SalesOrder.created_at.desc()).limit(20)
        return response({'rows':[{'id':o.id,'reference':o.internal_order_number,'channel':o.sales_channel or 'CRM','status':o.order_status,'payment_status':o.payment_status,'total':str(o.grand_total),'currency':o.currency,'created_local':service.local_time(o.created_at)} for o in rows],'limit':20})
    @router.post('/api/customer-registration/profile/{cid}/edit')
    async def edit(cid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        csrf(request)
        if user.role!='ADMIN':raise HTTPException(403,'Administrator identity review required')
        raw=await request.body()
        if len(raw)>4096:raise HTTPException(413,'Profile request too large')
        import json
        try:p=json.loads(raw)
        except ValueError:raise HTTPException(422,'Invalid profile request') from None
        from sqlalchemy.exc import IntegrityError
        try:c=service.edit_profile(db,user,cid,p);db.commit()
        except IntegrityError:
            db.rollback();raise HTTPException(409,'Concurrent identity conflict; reload and use authorized review') from None
        return response(service.describe(db,c))
    @router.post('/api/customer-registration/register')
    async def register(request:Request,db=Depends(get_db),user=Depends(require_user)):
        csrf(request)
        raw=await request.body()
        if len(raw)>4096:raise HTTPException(413,'Registration request too large')
        import json
        try:p=json.loads(raw)
        except ValueError:raise HTTPException(422,'Invalid registration request') from None
        if not isinstance(p,dict) or set(p)-{'name','phone','email','channel'}:raise HTTPException(422,'Only registration fields accepted')
        channel=p.get('channel','CRM');access(user,channel)
        if not request.headers.get('Idempotency-Key'):raise HTTPException(422,'Registration Idempotency-Key is required')
        try:c,created=service.register(db,user,p.get('name'),p.get('phone'),channel,request.headers.get('Idempotency-Key'),p.get('email'))
        except HTTPException as exc:
            if exc.status_code==409:db.commit()
            raise
        db.commit();return response({'customer':service.describe(db,c),'created':created,'marketing_consent_granted':False})
    return router
