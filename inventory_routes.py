"""Authenticated inventory UI and API; all local writes require existing CSRF protection."""
import json
import time
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import JSONResponse, Response
from models import Supplier
from inventory_models import InventoryDocument, InventoryPolicy
import inventory_service as service
import inventory_shopify as remote
import retail_service as retail
from retail_inventory import InventoryError
from settings import settings


def build_router(get_db,require_user,csrf,csrf_token,templates):
    router=APIRouter(prefix='/crm/retail')
    def output(value):return JSONResponse(value,headers={'Cache-Control':'private, no-store'})
    async def body(request):
        csrf(request)
        raw=await request.body()
        if len(raw)>64000:raise HTTPException(413,'Inventory request is too large')
        try:value=json.loads(raw)
        except (ValueError,TypeError):raise HTTPException(422,'JSON object required') from None
        if not isinstance(value,dict):raise HTTPException(422,'JSON object required')
        return value
    def safe(call):
        try:return call()
        except InventoryError as error:raise HTTPException(409,error.reason) from None

    @router.get('/inventory',include_in_schema=False)
    def screen(request:Request,user=Depends(require_user)):
        service.require(user)
        return templates.TemplateResponse('inventory.html',{'request':request,'user':user,'page_title':'Inventory Management',
            'csrf':csrf_token(request),'can_prepare':user.role=='ADMIN' or 'retail.inventory.prepare' in (user.section_access or []),
            'can_manage':user.role=='ADMIN' or 'retail.inventory.manage' in (user.section_access or []),'is_admin':user.role=='ADMIN'},headers={'Cache-Control':'private, no-store'})

    @router.get('/api/inventory/context')
    def context(db=Depends(get_db),user=Depends(require_user)):
        service.require(user)
        return output({'suppliers':[{'id':s.id,'name':s.name} for s in db.query(Supplier).order_by(Supplier.name)],
            'writes_enabled':bool(settings.SHOPIFY_INVENTORY_WRITE_ENABLED and remote.MUTATION_CONTRACT_VERIFIED),
            'source_contract_verified':remote.MUTATION_CONTRACT_VERIFIED,'native_transfers_verified':False,
            'physical_balance':'PROVISIONAL','max_lines':service.MAX_LINES})

    @router.get('/api/inventory/locations')
    def locations(user=Depends(require_user)):
        service.require(user)
        return output(safe(lambda:{'rows':remote.client.locations()}))

    @router.get('/api/inventory/capabilities')
    def capabilities(user=Depends(require_user)):
        service.require(user)
        return output(safe(remote.client.capabilities))

    @router.get('/api/inventory/products')
    def products(q:str='',vendor:str='',supplier_id:int|None=None,offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        service.require(user);query=retail.filtered(db,{'q':q,'vendor':vendor})
        if supplier_id is not None:query=query.filter(retail.RetailVariant.id.in_(db.query(InventoryPolicy.variant_id).filter_by(shop=retail.shop(),supplier_id=supplier_id)))
        ctx=retail.audit_context(db)
        return output({'total':query.count(),'rows':[retail.public(row,ctx) for row in query.order_by(retail.RetailVariant.id).offset(offset).limit(10)],
            'vendors':retail.filter_options(db)['vendors']})

    @router.get('/api/inventory/scan')
    def scan(code:str=Query(...,max_length=100),offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        service.require(user);return output(retail.scan_page(db,code,offset))

    @router.get('/api/inventory/stock/{rid}')
    def stock(rid:int,version:int=Query(...,ge=1),location:str=Query(...,max_length=100),db=Depends(get_db),user=Depends(require_user)):
        return output(safe(lambda:service.stock(db,user,rid,version,location)))

    @router.post('/api/inventory/report')
    async def report(request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user);p=await body(request)
        if set(p)!={'location','products'} or not isinstance(p['products'],list) or not 1<=len(p['products'])<=10:raise HTTPException(422,'Choose 1–10 products and one location')
        if any(not isinstance(row,dict) or set(row)!={'id','version'} for row in p['products']):raise HTTPException(422,'Invalid product snapshot')
        rows=[];deadline=time.monotonic()+16
        for selected in p['products']:
            line=service.identity(db,selected['id'],selected['version'],p['location'])
            observed=safe(lambda:service.stock(db,user,selected['id'],selected['version'],p['location'])) if time.monotonic()<deadline else {'status':'UNAVAILABLE','available':None,'on_hand':None,'reason':'REPORT_READ_BUDGET'}
            rows.append({**line,**observed})
        return output({'rows':rows,'atomic_snapshot':False,'valuation':None,'valuation_reason':'No approved costing policy or reconciled opening costs'})

    @router.get('/api/inventory/documents')
    def documents(offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        service.require(user);query=db.query(InventoryDocument).filter_by(shop=retail.shop())
        return output({'total':query.count(),'rows':[service.public(db,d) for d in query.order_by(InventoryDocument.created_at.desc()).offset(offset).limit(20)]})

    @router.post('/api/inventory/documents')
    async def create(request:Request,db=Depends(get_db),user=Depends(require_user)):
        payload=await body(request);doc=safe(lambda:service.create(db,user,payload));return output(service.public(db,doc))

    @router.get('/api/inventory/documents/{document_id}')
    def document(document_id:str,db=Depends(get_db),user=Depends(require_user)):
        service.require(user);return output(service.public(db,service.get(db,document_id)))

    @router.post('/api/inventory/documents/{document_id}/{action}')
    async def action(document_id:str,action:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request)
        if action=='count' and set(p)=={'line','quantity'}:doc=safe(lambda:service.observe_count(db,user,document_id,p['line'],p['quantity']))
        elif action=='submit' and p=={}:doc=safe(lambda:service.submit(db,user,document_id))
        elif action=='approve' and set(p)=={'confirmation'}:doc=service.approve(db,user,document_id,p['confirmation'])
        elif action=='reject' and set(p)=={'reason'}:doc=service.reject(db,user,document_id,p['reason'])
        elif action in {'dispatch','receive'} and set(p)=={'confirmation'}:doc=service.transfer_confirmation(db,user,document_id,action.upper(),p['confirmation'])
        elif action=='reconcile' and set(p)=={'confirmation','evidence'}:doc=safe(lambda:service.reconcile(db,user,document_id,p['evidence'],p['confirmation']))
        else:raise HTTPException(422,'Unknown action or invalid fields')
        return output(service.public(db,doc))

    @router.post('/api/inventory/policies')
    async def policy(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request);service.set_policy(db,user,p);return output({'saved':True})

    @router.get('/api/inventory/movements')
    def movements(format:str='json',db=Depends(get_db),user=Depends(require_user)):
        rows=service.movement_report(db,user)
        if format=='csv':return Response(service.csv_report(rows),media_type='text/csv',headers={'Cache-Control':'private, no-store','Content-Disposition':'attachment; filename="inventory-movements.csv"'})
        return output({'rows':rows,'limit_documents':500,'valuation':None,'valuation_reason':'Costing policy and opening cost data not established'})
    return router
