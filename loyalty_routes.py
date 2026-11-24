"""Permissioned management pages and existing CRM-order loyalty adapters."""
import csv,io,json
from datetime import datetime
from decimal import Decimal
from fastapi import APIRouter,Depends,Request,HTTPException,Query
from fastapi.responses import Response
from sqlalchemy import or_
from models import Customer,SalesOrder
from loyalty_models import *
from retail_models import RetailVariant
import staff_access
import loyalty_service as svc
import loyalty_reporting as reports

PAGES={'dashboard':('Dashboard','dashboard'),'members':('Loyalty Members','members.view'),'products':('Loyalty Products','products.manage'),'redemptions':('Redemption History','redemptions.view'),'migration':('Historical Customer Migration','migration.preview'),'reports':('Reports','redemptions.view'),'settings':('Settings','settings.manage')}

def require(user,key):
    if not staff_access.allowed(user,'loyalty.'+key):svc.fail('Loyalty permission required: '+key,403)
def can(user,key):return staff_access.allowed(user,'loyalty.'+key)
def safe_rows(rows,profit):
    if profit:return rows
    return [{k:v for k,v in row.items() if k not in {'gross_profit','gross_margin','contribution','estimated_gross_profit','estimated_contribution','cost','variable_cost'}} for row in rows]
def csv_response(rows,name):
    out=io.StringIO();fields=list(dict.fromkeys(k for row in rows for k in row)) or ['result'];writer=csv.DictWriter(out,fieldnames=fields);writer.writeheader()
    def cell(value):
        value=json.dumps(value,default=str,ensure_ascii=False) if isinstance(value,(dict,list)) else '' if value is None else str(value)
        return "'"+value if value.startswith(('=','+','-','@','\t','\r','\n')) else value
    writer.writerows({k:cell(v) for k,v in row.items()} for row in rows)
    return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="loyalty-{name}.csv"','Cache-Control':'private, no-store'})

def build_router(get_db,require_user,csrf,csrf_token,templates,verify_products,crm_require):
    router=APIRouter()
    def crm(user):crm_require(user)
    def json_response(value):
        from fastapi.responses import JSONResponse
        return JSONResponse(svc.serial(value),headers={'Cache-Control':'private, no-store'})
    async def payload(request):
        csrf(request)
        try:value=await request.json()
        except ValueError:svc.fail('Invalid JSON',400)
        if not isinstance(value,dict):svc.fail('JSON object required')
        return value
    @router.get('/crm/loyalty')
    @router.get('/crm/loyalty/{page}')
    def page(request:Request,page:str='dashboard',q:str='',status:str='',source:str='',start:str='',end:str='',channel:str='',minimum_orders:int=Query(0,ge=0),offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        if page not in PAGES:svc.fail('Page not found',404)
        title,permission=PAGES[page];require(user,permission)
        if channel and channel!='CRM':svc.fail('Loyalty redemptions are CRM only')
        data={};total=0
        if page=='dashboard':
            data=reports.metrics(db,start or None,end or None,channel or None)
            if not can(user,'reports.profit'):
                data.pop('estimated_gross_profit',None);data.pop('estimated_contribution',None)
        elif page=='members':
            rows=[];term=q.strip().casefold();normalized=svc.phone(q)
            for m in db.query(LoyaltyMember).order_by(LoyaltyMember.id):
                s=svc.member_summary(db,m.customer_id)
                if term and term not in str(s['name']).casefold() and term not in m.membership_id.casefold() and term not in (s['mobile'] or '') and (not normalized or normalized!=s['mobile']):continue
                if status and s['status']!=status or source and m.source!=source or s['qualifying_count']<minimum_orders:continue
                if start and (not m.activated_at or m.activated_at<svc.stamp(start)) or end and (not m.activated_at or m.activated_at>=svc.stamp(end)):continue
                rows.append({**s,'source':m.source})
            total=len(rows);data=rows[offset:offset+50]
        elif page=='products':data=[{'rule':svc.rule_data(r),'variant':db.get(RetailVariant,r.variant_id),'calculation':svc.rule_projection(db,r)} for r in db.query(LoyaltyRule).order_by(LoyaltyRule.id.desc()).limit(100)]
        elif page=='redemptions':
            rows=safe_rows(reports.redemption_rows(db,start or None,end or None),can(user,'reports.profit'));total=len(rows);data=rows[offset:offset+50]
        elif page=='migration':data={'jobs':list(db.query(LoyaltyMigration).order_by(LoyaltyMigration.created_at.desc()).limit(50)),'reviews':list(db.query(LoyaltyIdentityReview).filter_by(status='OPEN').order_by(LoyaltyIdentityReview.id).limit(100))}
        elif page=='reports':data={'kinds':['growth','repeat','products','customers','discounts','refunds','migrations']+(['profitability'] if can(user,'reports.profit') else [])}
        elif page=='settings':data=svc.config(db)
        return templates.TemplateResponse('loyalty.html',{'request':request,'user':user,'page_title':title,'page':page,'pages':PAGES,'data':data,'total':total,'offset':offset,'q':q,'start':start,'end':end,'channel':channel,'status':status,'source':source,'minimum_orders':minimum_orders,'config':svc.config(db),'defaults':svc.rules_config(db),'csrf':csrf_token(request),'loyalty_can':lambda p:can(user,p)},headers={'Cache-Control':'private, no-store'})
    @router.get('/crm/loyalty/members/{member_id}')
    def member(member_id:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        require(user,'members.view');m=db.get(LoyaltyMember,member_id)
        if not m:svc.fail('Member not found',404)
        data={'member':m,'summary':svc.member_summary(db,m.customer_id),'redemptions':safe_rows([r for r in reports.redemption_rows(db) if r['customer_id']==m.customer_id],can(user,'reports.profit')),'audits':db.query(LoyaltyAudit).filter_by(resource=m.membership_id).order_by(LoyaltyAudit.id.desc()).limit(100).all()}
        return templates.TemplateResponse('loyalty_member.html',{'request':request,'user':user,'page_title':m.membership_id,'data':data,'csrf':csrf_token(request),'can_manage':can(user,'members.manage')},headers={'Cache-Control':'private, no-store'})
    @router.post('/crm/loyalty/api/settings')
    async def settings(request:Request,db=Depends(get_db),user=Depends(require_user)):
        require(user,'settings.manage');value=await payload(request);current=svc.config(db)
        if 'enabled' in value and value['enabled']!=bool(current and current.enabled):
            require(user,'programme.enable')
            if value['enabled']:
                from settings import settings
                if not settings.RETAIL_ENABLED:svc.fail('Existing Retail worker must already be enabled before loyalty activation',409)
        c=svc.update_config(db,user,value);db.commit();return json_response({'enabled':c.enabled,'version':c.version,'value':c.value})
    @router.post('/crm/loyalty/api/members/{customer_id}')
    async def member_action(customer_id:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        require(user,'members.manage');v=await payload(request);svc.change_member(db,user,customer_id,v.get('action'),v.get('reason'),v.get('notes'),v.get('shopify_customer_id'));db.commit();return json_response(svc.member_summary(db,customer_id))
    @router.post('/crm/loyalty/api/recalculate')
    async def retry_recalculation(request:Request,db=Depends(get_db),user=Depends(require_user)):
        require(user,'members.manage');v=await payload(request)
        job=db.query(LoyaltyRecalculation).filter_by(customer_id=v.get('customer_id')).with_for_update().one_or_none()
        if not job:svc.fail('No pending/review local recalculation for this customer',404)
        why=svc.reason(v.get('reason'));previous={'status':job.status,'attempts':job.attempts,'error':job.error}
        job.status='PENDING';job.attempts=0;job.error=None
        svc.audit(db,'LOCAL_RECALCULATION_RETRY',job.customer_id,user.username,previous,{'status':'PENDING'},why);db.commit()
        return json_response({'status':'PENDING','external_operations':0})
    @router.post('/crm/loyalty/api/order-exclusions')
    async def exclude_order(request:Request,db=Depends(get_db),user=Depends(require_user)):
        require(user,'members.manage');v=await payload(request);svc.config(db,True)
        order=db.get(SalesOrder,v.get('order_id'))
        if not order:svc.fail('Existing order ID required',404)
        prior=db.get(LoyaltyOrderExclusion,order.id)
        if not prior:
            why=svc.reason(v.get('reason'));db.add(LoyaltyOrderExclusion(order_id=order.id,actor_id=user.id,reason=why));svc.audit(db,'TEST_ORDER_EXCLUDED',order.id,user.username,None,{'order_id':order.id},why);db.flush()
            svc.sync_redemptions(db,order,user.username)
            cid=svc.order_customer(db,order)
            if cid:svc.refresh_member(db,cid,user.username)
        db.commit();return json_response({'status':'EXCLUDED_FROM_LOYALTY_ONLY','commercial_order_unchanged':True})
    @router.post('/crm/loyalty/api/identity/{review_id}')
    async def identity(review_id:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        require(user,'members.manage');v=await payload(request);svc.resolve_order_identity(db,user,review_id,v.get('customer_id'),v.get('reason'));db.commit();return json_response({'status':'RESOLVED'})
    @router.get('/crm/loyalty/api/products')
    def products(q:str=Query('',max_length=100),offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        require(user,'products.manage')
        from settings import settings
        query=db.query(RetailVariant).filter_by(shop=(settings.SHOPIFY_SHOP or '').lower())
        if q:query=query.filter(or_(RetailVariant.title.ilike('%'+q+'%'),RetailVariant.variant_title.ilike('%'+q+'%'),RetailVariant.sku==q,RetailVariant.barcode==q,RetailVariant.local_barcode==q,RetailVariant.product_type.ilike('%'+q+'%'),RetailVariant.category==q,RetailVariant.collections.cast(__import__('sqlalchemy').String).ilike('%'+q+'%')))
        rows=query.order_by(RetailVariant.id).offset(offset).limit(51).all()
        return json_response({'rows':[{'id':r.id,'product_id':r.product_id,'variant_id':r.variant_id,'name':r.title,'variant':r.variant_title,'sku':r.sku,'barcode':r.barcode,'image':r.image,'price':r.price,'compare_at_price':r.compare_at_price,'currency':r.currency,'state':r.product_status,'missing':r.missing,'stale':r.stale} for r in rows[:50]],'has_more':len(rows)>50})
    @router.post('/crm/loyalty/api/rules')
    async def rules(request:Request,db=Depends(get_db),user=Depends(require_user)):
        require(user,'products.manage');v=await payload(request);rows=svc.save_rules(db,user,v,can(user,'exceptions.approve'));db.commit();return json_response({'rows':rows})
    @router.post('/crm/loyalty/api/rules/preview')
    async def preview_rule(request:Request,db=Depends(get_db),user=Depends(require_user)):
        require(user,'products.manage');v=await payload(request);rows=[]
        ids=v.get('variant_ids',[])
        if not isinstance(ids,list) or not 1<=len(ids)<=100:svc.fail('Choose 1–100 variants')
        for rid in ids:
            from settings import settings
            variant=db.get(RetailVariant,rid)
            if not variant or variant.shop!=(settings.SHOPIFY_SHOP or '').lower() or variant.price is None:svc.fail('Imported variant not found',404)
            if v.get('method') not in {'PERCENT','AMOUNT','PRICE'}:svc.fail('Invalid method')
            rule=LoyaltyRule(method=v['method'],value=svc.money(v.get('value')),cost=svc.money(v['cost']) if v.get('cost') not in (None,'') else None,cost_reference=v.get('cost_reference'),cost_verified_at=svc.stamp(v['cost_verified_at']) if v.get('cost_verified_at') else None,variable_cost=svc.money(v['variable_cost']) if v.get('variable_cost') not in (None,'') else None)
            rows.append({'variant_id':rid,'name':variant.title,'calculation':svc.calculation(db,rule,variant.price)})
        return json_response({'rows':rows})
    @router.post('/crm/loyalty/api/migrations')
    async def migration_preview(request:Request,db=Depends(get_db),user=Depends(require_user)):
        require(user,'migration.preview');v=await payload(request);job=svc.preview_migration(db,user,v,request.headers.get('Idempotency-Key'));db.commit();return json_response({'id':job.id,'status':job.status,'digest':job.digest,'counts':job.counts})
    @router.get('/crm/loyalty/api/migrations/{job_id}')
    def migration(job_id:str,db=Depends(get_db),user=Depends(require_user)):
        require(user,'migration.preview');job=db.get(LoyaltyMigration,job_id)
        if not job:svc.fail('Migration not found',404)
        return json_response({'id':job.id,'status':job.status,'digest':job.digest,'counts':job.counts,'outcomes':{s:db.query(LoyaltyMigrationRow).filter_by(migration_id=job.id,status=s).count() for s in ('PROPOSED','EXECUTED','REVIEW','INELIGIBLE')},'rows':[{'customer_id':r.customer_id,'order_id':r.order_id,'status':r.status,'detail':r.detail} for r in db.query(LoyaltyMigrationRow).filter_by(migration_id=job.id).order_by(LoyaltyMigrationRow.id).limit(100)]})
    @router.post('/crm/loyalty/api/migrations/{job_id}/{action}')
    async def migration_action(job_id:str,action:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        require(user,'migration.execute');v=await payload(request)
        if action=='approve':job=svc.approve_migration(db,user,job_id,v.get('digest'),v.get('reason'))
        elif action=='execute':job=svc.execute_migration_batch(db,user,job_id,v.get('digest'))
        else:svc.fail('Unknown migration action')
        db.commit();return json_response({'id':job.id,'status':job.status,'digest':job.digest})
    @router.get('/crm/loyalty/api/reports/{kind}')
    def report(kind:str,start:str='',end:str='',channel:str='',format:str='json',db=Depends(get_db),user=Depends(require_user)):
        require(user,'redemptions.view')
        if kind in {'growth','repeat','customers'}:require(user,'members.view')
        if kind=='migrations':require(user,'migration.preview')
        if kind=='profitability':require(user,'reports.profit')
        if format not in {'csv','json'}:svc.fail('Choose CSV or JSON')
        if channel and channel!='CRM':svc.fail('Loyalty redemptions are CRM only')
        rows=safe_rows(reports.report(db,kind,start or None,end or None,channel or None),can(user,'reports.profit'))
        if format=='csv':require(user,'reports.export');return csv_response(rows,kind)
        return json_response({'rows':rows})
    @router.get('/crm/loyalty/api/migrations/{job_id}/csv')
    def migration_csv(job_id:str,db=Depends(get_db),user=Depends(require_user)):
        require(user,'migration.preview');require(user,'reports.export')
        if not db.get(LoyaltyMigration,job_id):svc.fail('Migration not found',404)
        return csv_response([{'customer_id':r.customer_id,'order_id':r.order_id,'status':r.status,**r.detail} for r in db.query(LoyaltyMigrationRow).filter_by(migration_id=job_id)],'migration')
    @router.get('/api/sales-orders/loyalty/customers')
    def customer_search(q:str=Query('',max_length=100),db=Depends(get_db),user=Depends(require_user)):
        crm(user)
        if len(q.strip())<2:return json_response({'rows':[]})
        rows=db.query(Customer).filter(or_(Customer.name.ilike('%'+q.strip()+'%'),Customer.phone.ilike('%'+q.strip()+'%'))).order_by(Customer.id).limit(20).all()
        normalized=svc.phone(q)
        if normalized:
            rows=[c for c in db.query(Customer).yield_per(200) if svc.phone(c.phone)==normalized][:20]
        return json_response({'rows':[{'id':c.id,'name':c.name,'mobile':svc.phone(c.phone)} for c in rows]})
    @router.get('/api/sales-orders/loyalty/customers/{cid}')
    def customer_summary(cid:int,db=Depends(get_db),user=Depends(require_user)):
        crm(user);s=svc.member_summary(db,cid)
        if s['reason']=='CUSTOMER_NOT_FOUND':svc.fail('Customer not found',404)
        if not can(user,'members.view'):s.pop('history',None);s.pop('candidates',None)
        s['can_view_member']=can(user,'members.view');return json_response(s)
    @router.post('/api/sales-orders/loyalty/quote')
    async def crm_quote(request:Request,db=Depends(get_db),user=Depends(require_user)):
        crm_require(user,True);v=await payload(request)
        from sales_order_service import SalesOrderValidationError
        try:verified=verify_products(v)
        except SalesOrderValidationError as exc:svc.fail(str(exc))
        if not isinstance(v.get('customer_id'),int) or isinstance(v.get('customer_id'),bool):svc.fail('Choose an existing customer')
        order=None
        if v.get('order_id'):
            order=db.get(SalesOrder,v['order_id'])
            if not order or order.order_status!='DRAFT' or order.sales_channel=='POS':svc.fail('Only a mutable CRM draft can be repriced',409)
        # No membership or rule is created by a quote preview.
        result=svc.quote(db,v['customer_id'],verified['lines'],order.id if order else None,v.get('order_discount',0))
        from sales_order_service import calculate_order
        priced=[{**raw,'discount_amount':line['total_line_discount']} for raw,line in zip(verified['lines'],result['lines'])]
        totals=calculate_order(priced,0,v.get('shipping_total',0),v.get('tax_total',0),v.get('service_total',0))
        result['totals']={key:str(getattr(totals,key)) for key in ('subtotal','discount_total','shipping_total','service_total','tax_total','grand_total')}
        return json_response(svc.public_quote(result))
    return router
