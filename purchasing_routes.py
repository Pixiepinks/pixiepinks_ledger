"""Purchasing screens and CSRF-protected endpoints; Shopify reads use existing adapter."""
import json
from fastapi import APIRouter,Depends,Request,HTTPException,Query
from fastapi.responses import JSONResponse
from models import Supplier
from purchasing_models import PurchaseRecord as Record
from retail_inventory import InventoryError
import purchasing_service as service
import retail_service as retail
import inventory_shopify as remote
from settings import settings

def build_router(get_db,require_user,csrf,csrf_token,templates):
    router=APIRouter(prefix='/crm/retail')
    def output(value):return JSONResponse(value,headers={'Cache-Control':'private, no-store'})
    async def body(request):
        csrf(request);raw=await request.body()
        if len(raw)>64000:raise HTTPException(413,'Purchasing request too large')
        try:data=json.loads(raw)
        except ValueError:raise HTTPException(422,'JSON object required') from None
        if not isinstance(data,dict):raise HTTPException(422,'JSON object required')
        return data
    def safe(fn):
        try:return fn()
        except InventoryError as e:raise HTTPException(409,e.reason) from None
        except (ValueError,TypeError,KeyError):raise HTTPException(422,'Invalid purchasing fields; reload and check required inputs') from None

    @router.get('/purchases',include_in_schema=False)
    def screen(request:Request,user=Depends(require_user)):
        service.require(user)
        return templates.TemplateResponse('purchasing.html',{'request':request,'user':user,'csrf':csrf_token(request),'page_title':'Purchasing & Goods Receiving'},headers={'Cache-Control':'private, no-store'})

    @router.get('/api/purchases/context')
    def context(db=Depends(get_db),user=Depends(require_user)):
        service.require(user)
        try:
            verified={r['id']:r for r in remote.client.locations() if r.get('isActive') is True};location_error=None
        except InventoryError:
            verified={};location_error='Location verification unavailable. Retry or ask an administrator to check the Shopify connection.'
        mapped=[service.public(db,r) for r in db.query(Record).filter_by(shop=retail.shop(),kind='LOCATION')]
        for row in mapped:
            gid=row['payload']['location_id'];row['verified_active']=gid in verified
            row['name']=verified.get(gid,{}).get('name',row['payload'].get('name','Unverified location'))
        balances={}
        from decimal import Decimal
        from pos_money import fmt
        for bill in db.query(Record).filter_by(shop=retail.shop(),kind='BILL',status='APPROVED'):
            balances[bill.supplier_id]=balances.get(bill.supplier_id,Decimal(0))+Decimal(bill.payload['total'])
        for payment in db.query(Record).filter_by(shop=retail.shop(),kind='PAYMENT'):
            balances[payment.supplier_id]=balances.get(payment.supplier_id,Decimal(0))-Decimal(payment.payload['amount'])
        return output({'location_error':location_error,'suppliers':[{'id':s.id,'name':s.name,'email':s.email,'phone':s.phone,'balance':fmt(balances.get(s.id,Decimal(0))),**service.supplier_profile(db,s.id)} for s in db.query(Supplier).order_by(Supplier.name)],
        'locations':mapped,
        'inventory_sync_enabled':bool(settings.PURCHASE_INVENTORY_SYNC_ENABLED and settings.SHOPIFY_INVENTORY_WRITE_ENABLED),
        'permissions':{p:user.role=='ADMIN' or p in (user.section_access or []) for p in service.PERMISSIONS},'administrator':user.role=='ADMIN'})

    @router.post('/api/purchases/preview')
    async def preview(request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,service.PERMISSIONS[1]);data=await body(request)
        return output(safe(lambda:service.quote(db,{**data,'invoice':data.get('invoice') or 'UNSAVED PREVIEW'},verify_remote=False)))

    @router.get('/api/purchases/products')
    def products(q:str='',offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        service.require(user);query=retail.filtered(db,{'q':q});ctx=retail.audit_context(db)
        return output({'total':query.count(),'rows':[retail.public(r,ctx) for r in query.order_by(retail.RetailVariant.id).offset(offset).limit(20)]})

    @router.get('/api/purchases/shopify-locations')
    def locations(user=Depends(require_user)):
        service.require(user,service.PERMISSIONS[3],admin=True);return output(safe(lambda:remote.client.locations()))

    @router.get('/api/purchases')
    def records(kind:str='BILL',offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        service.require(user)
        if kind not in {'BILL','RECEIPT','PAYMENT','SUPPLIER','LOCATION'}:raise HTTPException(422,'Invalid record type')
        query=db.query(Record).filter_by(shop=retail.shop(),kind=kind)
        return output({'total':query.count(),'rows':[service.public(db,r) for r in query.order_by(Record.created_at.desc()).offset(offset).limit(50)]})

    @router.get('/api/purchases/{rid}')
    def record(rid:str,db=Depends(get_db),user=Depends(require_user)):
        service.require(user);return output(service.public(db,service.get(db,rid)))

    @router.get('/api/purchases/stock-comparison/{document_id}')
    def comparison(document_id:str,db=Depends(get_db),user=Depends(require_user)):
        return output(safe(lambda:service.comparison(db,user,document_id)))

    @router.post('/api/purchases/directory/{kind}')
    async def directory(kind:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        if kind not in {'SUPPLIER','LOCATION'}:raise HTTPException(422,'Invalid directory')
        data=await body(request);return output(safe(lambda:service.public(db,service.put_directory(db,user,kind,data))))

    @router.post('/api/purchases/create/{kind}')
    async def create(kind:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        data=await body(request);return output(safe(lambda:service.public(db,service.create(db,user,kind,data))))

    @router.post('/api/purchases/{rid}/{action}')
    async def action(rid:str,action:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        data=await body(request)
        if action=='edit':return output(safe(lambda:service.public(db,service.edit_bill(db,user,rid,data))))
        if action=='retry':
            if data.get('confirmation')!=rid:raise HTTPException(422,'Confirm exact inventory document ID')
            safe(lambda:service.retry(db,user,rid));return output({'status':'Pending'})
        return output(safe(lambda:service.public(db,service.action(db,user,rid,action,data.get('confirmation')))))
    return router
