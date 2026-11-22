"""Dedicated cashier surface with existing sessions, section grants and CSRF."""
import json,csv,io
from decimal import Decimal
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,Request,HTTPException,Query
from fastapi.responses import JSONResponse,Response
from models import Customer,Account,SalesOrder
from pos_models import *
import pos_service as service
import pos_shopify as remote
import pos_receipts
import pos_provisional_receipts
import retail_service as retail
from settings import settings


def build_router(get_db,require_user,csrf,csrf_token,templates):
    router=APIRouter(prefix='/crm/pos')
    def out(data):return JSONResponse(data,headers={'Cache-Control':'private, no-store'})
    async def body(request,keys):
        csrf(request);raw=await request.body()
        if len(raw)>64000:raise HTTPException(413,'POS request too large')
        try:p=json.loads(raw)
        except (ValueError,TypeError):raise HTTPException(422,'POS JSON required') from None
        if not isinstance(p,dict) or set(p)!=set(keys):raise HTTPException(422,'Unexpected/missing POS fields')
        return p
    def cart(c):return {'id':c.id,'version':c.version,'status':c.status,'payload':c.payload,'quote':c.quote,'session_id':c.session_id}
    def register_session(db,s):return {'movements':[{'kind':m.kind,'amount':str(m.amount),'reference':m.reference,'detail':m.detail,'at':m.created_at.isoformat()+'Z'} for m in db.query(POSCashMovement).filter_by(session_id=s.id)],'id':s.id,'cashier_id':s.cashier_id,'register_id':s.register_id,'location':db.get(POSRegister,s.register_id).location_id,'status':s.status,'opening_float':str(s.opening_float),'expected_cash':str(service.drawer(db,s)),'closing':s.closing}

    @router.get('',include_in_schema=False)
    def screen(request:Request,user=Depends(require_user)):
        service.require(user);return templates.TemplateResponse('pos.html',{'request':request,'user':user,'page_title':'Point of Sale','csrf':csrf_token(request),
            'pos_permissions':[k for k in service.PERMISSIONS if user.role=='ADMIN' or 'pos.'+k in (user.section_access or [])]},headers={'Cache-Control':'private, no-store'})

    @router.get('/api/context')
    def context(db=Depends(get_db),user=Depends(require_user)):
        service.require(user);policy,version=service.cfg(db)
        recovery=db.query(POSCheckout).join(POSSession,POSSession.id==POSCheckout.session_id).filter(
            POSCheckout.shop==retail.shop(),POSSession.shop==retail.shop(),POSSession.cashier_id==user.id,
            POSCheckout.status.notin_(['COMPLETE','CANCELLED'])).order_by(POSCheckout.created_at.desc())
        return out({'shop':retail.shop(),'recovery_sales':[{'id':r.id,'reference':r.reference,'status':r.status} for r in recovery],
            'cashier':user.username,'cashier_id':user.id,'checkout_enabled':settings.POS_CHECKOUT_WRITE_ENABLED,'refund_enabled':settings.POS_REFUND_WRITE_ENABLED,
            'daily_operations_enabled':settings.POS_DAILY_OPERATIONS_ENABLED,'daily_registers':[{'register_id':p.register_id,'enabled':bool(p.enabled),'device_id':p.device_id,'device_version':p.device_version} for p in db.query(POSDailyPolicy).filter_by(shop=retail.shop())],
            'config':policy,'config_version':version,'contract':remote.CONTRACT,'currency':'LKR','physical_balance':'PROVISIONAL',
            'registers':[{'id':r.id,'name':r.name,'location':r.location_id} for r in db.query(POSRegister).filter_by(shop=retail.shop())],
            'sessions':[register_session(db,s) for s in db.query(POSSession).filter_by(shop=retail.shop(),cashier_id=user.id,status='OPEN')],
            'categories':retail.filter_options(db)['categories']})

    @router.get('/api/daily')
    def daily_setup(db=Depends(get_db),user=Depends(require_user)):
        from hardware_models import PrintDevice
        service.require(user,'admin')
        return out({'enabled':settings.POS_DAILY_OPERATIONS_ENABLED,
            'policies':[{'register_id':p.register_id,'device_id':p.device_id,'device_version':p.device_version,'version':p.version,'enabled':bool(p.enabled)} for p in db.query(POSDailyPolicy).filter_by(shop=retail.shop())],
            'devices':[{'id':d.id,'name':d.name,'register_id':d.register_id,'location':d.location_id,'version':d.version} for d in db.query(PrintDevice).filter_by(shop=retail.shop(),status='ACTIVE') if d.config.get('receipt_only')]})

    @router.post('/api/daily')
    async def configure_daily(request:Request,db=Depends(get_db),user=Depends(require_user)):
        import pos_daily
        p=await body(request,['device_id','enabled','notes','confirm']);row=pos_daily.configure(db,user,**p)
        return out({'register_id':row.register_id,'device_id':row.device_id,'version':row.version,'enabled':bool(row.enabled)})

    @router.get('/api/real-pilot')
    def pilot_status(db=Depends(get_db),user=Depends(require_user)):
        import pos_real_sale_pilot as pilot
        from hardware_models import PrintDevice
        pilot.require(user);s=pilot.scope(db)
        return out({'enabled':True,'scope':{'shop':s.shop,'register_id':s.register_id,'device_id':s.device_id,'location_id':s.location_id} if s else None,
            'devices':[{'id':d.id,'register_id':d.register_id,'name':d.name} for d in db.query(PrintDevice).filter_by(shop=retail.shop(),status='ACTIVE') if d.config.get('receipt_only')]})

    @router.post('/api/real-pilot/scope')
    async def pilot_scope(request:Request,db=Depends(get_db),user=Depends(require_user)):
        import pos_real_sale_pilot as pilot
        p=await body(request,['device_id','notes','confirm']);s=pilot.configure(db,user,**p)
        return out({'shop':s.shop,'register_id':s.register_id,'device_id':s.device_id})

    @router.post('/api/real-pilot/authorize')
    async def pilot_authorize(request:Request,db=Depends(get_db),user=Depends(require_user)):
        import pos_real_sale_pilot as pilot
        p=await body(request,['cid','version','key','notes','confirm']);a=pilot.authorize(db,user,**p)
        return out({'cart_id':a.cart_id,'device_id':a.device_id,'expires_at':a.expires_at.isoformat()+'Z'})

    @router.get('/api/capabilities')
    def capabilities(user=Depends(require_user)):
        service.require(user)
        try:return out(remote.client.capabilities())
        except Exception:return out({'checkout_ready':False,'refund_ready':False,'reason':'Live Shopify compatibility unavailable'})

    @router.post('/api/config')
    async def configure(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['value']);service.configure(db,user,p['value']);return out({'saved':True})

    @router.get('/api/locations')
    def locations(user=Depends(require_user)):
        service.require(user,'admin');return out({'rows':service.inventory.remote.client.locations()})

    @router.get('/api/accounts')
    def accounts(db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'admin');return out({'rows':[{'id':a.id,'name':a.name,'type':a.type,'code':a.code} for a in db.query(Account).order_by(Account.code)]})

    @router.post('/api/registers')
    async def register(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['name','location']);r=service.register(db,user,p['name'],p['location']);return out({'id':r.id})
    @router.post('/api/sessions')
    async def opening(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['register_id','opening_float']);return out(register_session(db,service.open_register(db,user,p['register_id'],p['opening_float'])))
    @router.post('/api/sessions/{sid}/close')
    async def close(sid:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['counted','notes']);return out(register_session(db,service.close_register(db,user,sid,p['counted'],p['notes'])))
    @router.post('/api/sessions/{sid}/cash')
    async def cash(sid:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['amount','kind','key','note','offset_account']);service.cash_move(db,user,sid,p['amount'],p['kind'],p['key'],p['note'],p['offset_account']);return out({'saved':True})

    @router.get('/api/products')
    def products(q:str='',category:str='',barcode:str|None=None,offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        service.require(user)
        if barcode is not None:return out(retail.scan_page(db,barcode,offset))
        return out(retail.page(db,{'q':q,'category':category},offset,24))

    @router.get('/api/stock/{rid}')
    def stock(rid:int,version:int,sid:str,db=Depends(get_db),user=Depends(require_user)):
        service.require(user);s=service.session(db,user,sid);location=db.get(POSRegister,s.register_id).location_id
        try:
            line=service.inventory.identity(db,rid,version,location);observed=remote.client.inspect(line)
            return out({'status':'VERIFIED_LIVE','physical_balance':'PROVISIONAL',**observed})
        except (remote.POSRemoteError,service.inventory.InventoryError):return out({'status':'UNAVAILABLE','available':None,'on_hand':None})

    @router.get('/api/customers')
    def customers(q:str=Query(...,min_length=2,max_length=100),db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'sell');escaped=q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        rows=db.query(Customer).filter((Customer.name.ilike('%'+escaped+'%',escape='\\'))|(Customer.phone.ilike('%'+escaped+'%',escape='\\'))).order_by(Customer.id).limit(20)
        return out({'rows':[{'id':r.id,'name':r.name,'phone':r.phone} for r in rows]})
    @router.post('/api/customers')
    async def new_customer(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['name','phone','consent']);r=service.create_customer(db,user,p['name'],p['phone'],p['consent']);return out({'id':r.id,'name':r.name,'phone':r.phone})
    @router.post('/api/customer-bindings')
    async def bind(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['customer_id','shopify_customer_id']);r=service.bind_customer(db,user,**p);return out({'id':r.id})

    @router.get('/api/customers/{rid}/history')
    def customer_history(rid:int,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'sell');rows=db.query(POSCheckout).join(SalesOrder,SalesOrder.id==POSCheckout.sales_order_id).filter(POSCheckout.shop==retail.shop(),SalesOrder.customer_id==rid).order_by(POSCheckout.created_at.desc()).limit(20)
        return out({'rows':[service.public_sale(db,r) for r in rows]})

    @router.get('/api/carts')
    def carts(db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'hold');rows=db.query(POSCart).filter_by(shop=retail.shop(),actor_id=user.id,status='HELD').order_by(POSCart.updated_at.desc()).limit(50)
        return out({'rows':[cart(c) for c in rows]})
    @router.post('/api/carts')
    async def new(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['session_id']);return out(cart(service.new_cart(db,user,p['session_id'])))
    @router.get('/api/carts/{cid}')
    def get(cid:str,db=Depends(get_db),user=Depends(require_user)):
        c=service.get_cart(db,user,cid);checkout=db.query(POSCheckout).filter_by(cart_id=c.id).one_or_none()
        return out({**cart(c),'checkout_id':checkout.id if checkout else None})
    @router.post('/api/carts/{cid}/{action}')
    async def edit(cid:str,action:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['version','payload'])
        if action=='authorize':c=service.authorize_discount(db,user,cid,p['version'],p['payload'].get('reason'))
        else:c=service.edit_cart(db,user,cid,p['version'],action,p['payload'])
        return out(cart(c))
    @router.post('/api/carts/{cid}/checkout/confirm')
    async def checkout(cid:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['version','key','tenders']);return out(service.public_sale(db,service.checkout(db,user,cid,p['version'],p['key'],p['tenders'])))

    @router.get('/api/sales')
    def history(start:str|None=None,end:str|None=None,cashier:int|None=None,method:str='',customer:int|None=None,product:str='',q:str='',offset:int=Query(0,ge=0),format:str='json',db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'reports')
        from datetime import date,time,timezone
        today=datetime.now(ZoneInfo('Asia/Colombo')).date()
        try:first=date.fromisoformat(start) if start else today;last=date.fromisoformat(end) if end else first
        except ValueError:raise HTTPException(422,'Valid ISO date range required') from None
        if first>last or (last-first).days>31:raise HTTPException(422,'Report range must be at most 31 days')
        low=datetime.combine(first,time.min,ZoneInfo('Asia/Colombo')).astimezone(timezone.utc).replace(tzinfo=None);high=datetime.combine(last+timedelta(days=1),time.min,ZoneInfo('Asia/Colombo')).astimezone(timezone.utc).replace(tzinfo=None)
        rows=db.query(POSCheckout).filter(POSCheckout.shop==retail.shop(),POSCheckout.created_at>=low,POSCheckout.created_at<high)
        if cashier is not None:rows=rows.filter(POSCheckout.actor_id==cashier)
        if customer is not None:rows=rows.join(SalesOrder,SalesOrder.id==POSCheckout.sales_order_id).filter(SalesOrder.customer_id==customer)
        if q:rows=rows.filter(POSCheckout.reference.contains(q,autoescape=True))
        if method:rows=rows.filter(POSCheckout.sales_order_id.in_(db.query(service.SalesPayment.sales_order_id).filter_by(payment_method=method)))
        if product:rows=rows.filter(POSCheckout.sales_order_id.in_(db.query(service.SalesOrderLine.sales_order_id).filter(service.SalesOrderLine.product_name_snapshot.contains(product,autoescape=True))))
        total=rows.count()
        if format=='summary':
            if total>1000:raise HTTPException(422,'Narrow summary to at most 1000 sales')
            totals={k:Decimal(0) for k in ['subtotal','discount_total','tax_total','grand_total','refunds']};by_cashier={};by_method={};by_product={};states={}
            for r in rows:
                states[r.status]=states.get(r.status,0)+1
                if r.status!='COMPLETE':continue
                quote=r.frozen['quote']
                for k in totals:
                    if k!='refunds':totals[k]+=Decimal(quote[k])
                by_cashier[r.frozen['cashier']]=by_cashier.get(r.frozen['cashier'],Decimal(0))+Decimal(quote['grand_total'])
                for tender in r.frozen['tenders']:by_method[tender['method']]=by_method.get(tender['method'],Decimal(0))+Decimal(tender['amount'])
                for line in quote['lines']:
                    key=line['variant_id'];entry=by_product.setdefault(key,{'title':line['title'],'quantity':0,'total':Decimal(0)})
                    entry['quantity']+=line['quantity'];entry['total']+=Decimal(line['total'])
                for refund in db.query(POSRefund).filter_by(checkout_id=r.id,status='PAID_OUT'):totals['refunds']+=Decimal(refund.plan['amount'])
            return out({'basis':'Sale date in Sri Lanka; refunds attributed to original sale; payout dates appear in register movements','totals':{k:str(v) for k,v in totals.items()},'cashiers':{k:str(v) for k,v in by_cashier.items()},'tenders':{k:str(v) for k,v in by_method.items()},'products':{k:{**v,'total':str(v['total'])} for k,v in by_product.items()},'states':states})
        if format=='csv':
            if total>1000:raise HTTPException(422,'Narrow the report to at most 1000 sales')
            data=[service.public_sale(db,r) for r in rows.order_by(POSCheckout.created_at).all()];stream=io.StringIO();writer=csv.writer(stream);writer.writerow(['Sale','Status','Cashier','Subtotal','Discount','Tax','Total','Shopify order'])
            def cell(v):
                value=str(v or '');return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
            for r in data:writer.writerow([cell(v) for v in [r['reference'],r['status'],r['frozen']['cashier'],*[r['frozen']['quote'][k] for k in ['subtotal','discount_total','tax_total','grand_total']],r['shopify_order_name']]])
            return Response(stream.getvalue(),media_type='text/csv',headers={'Cache-Control':'private, no-store','Content-Disposition':'attachment; filename="pos-sales.csv"'})
        return out({'total':total,'rows':[service.public_sale(db,r) for r in rows.order_by(POSCheckout.created_at.desc()).offset(offset).limit(25)]})
    @router.get('/api/sales/{rid}')
    def sale(rid:str,db=Depends(get_db),user=Depends(require_user)):return out(service.public_sale(db,service.sale(db,user,rid)))
    @router.post('/api/sales/{rid}/{action}')
    async def sale_action(rid:str,action:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        if action=='verify-bank':
            p=await body(request,['payment_id','key']);r=service.verify_bank(db,user,rid,p['payment_id'],p['key'])
        elif action=='finalize':p=await body(request,[]);r=service.finalize(db,user,rid)
        elif action=='void':p=await body(request,['notes']);r=service.cancel_checkout(db,user,rid,p['notes'])
        else:raise HTTPException(404,'Unknown POS action')
        return out(service.public_sale(db,r))
    @router.post('/api/operations/{oid}/reconcile')
    async def reconcile(oid:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['notes']);return out(service.public_sale(db,service.reconcile(db,user,oid,p['notes'])))
    @router.post('/api/sales/{rid}/refunds/create')
    async def refund(rid:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['session_id','key','items','method','restock','reason']);r=service.prepare_refund(db,user,rid,**p);return out({'id':r.id,'status':r.status})
    @router.post('/api/refunds/{fid}/payout')
    async def payout(fid:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['reference']);r=service.confirm_payout(db,user,fid,p['reference']);return out({'id':r.id,'status':r.status})
    @router.post('/api/sales/{rid}/receipt/pdf')
    async def receipt(rid:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['profile','refund_id']);pdf=pos_receipts.generate(db,user,rid,p['profile'],p['refund_id']);return Response(pdf,media_type='application/pdf',headers={'Cache-Control':'private, no-store','Content-Disposition':'inline; filename="pixiepinks-receipt.pdf"'})
    @router.post('/api/sales/{rid}/provisional/pdf')
    async def provisional(rid:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,['confirm_received'])
        pdf=pos_provisional_receipts.generate(db,user,rid,p['confirm_received'])
        return Response(pdf,media_type='application/pdf',headers={'Cache-Control':'private, no-store','Content-Disposition':'inline; filename="pixiepinks-provisional-80.pdf"'})
    @router.get('/api/sessions')
    def sessions(db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'reports');rows=db.query(POSSession).filter_by(shop=retail.shop()).order_by(POSSession.opened_at.desc()).limit(100)
        return out({'rows':[register_session(db,s) for s in rows]})
    return router
