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
        return output({'suppliers':[{'id':s.id,'name':s.name,'email':s.email,'phone':s.phone,**service.supplier_profile(db,s.id)} for s in db.query(Supplier).order_by(Supplier.name)],
        'locations':[service.public(db,r) for r in db.query(Record).filter_by(shop=retail.shop(),kind='LOCATION')],
        'inventory_sync_enabled':bool(settings.PURCHASE_INVENTORY_SYNC_ENABLED and settings.SHOPIFY_INVENTORY_WRITE_ENABLED),
        'permissions':{p:user.role=='ADMIN' or p in (user.section_access or []) for p in service.PERMISSIONS},'administrator':user.role=='ADMIN'})

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
