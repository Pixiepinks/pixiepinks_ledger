"""Retail UI/API using existing sessions, CSRF, templates and section grants."""
import json
import secrets
from datetime import datetime,timedelta
from fastapi import APIRouter,Depends,HTTPException,Request,Query
from fastapi.responses import Response,StreamingResponse,JSONResponse
from sqlalchemy.exc import IntegrityError
from retail_models import *
import retail_service as service
import retail_labels as labels
import retail_sync as sync
import retail_barcode_sync as barcode_sync
import retail_inventory
import retail_barcode_p1_pilot as p1_pilot
from starlette.concurrency import run_in_threadpool


def build_router(get_db,require_user,csrf,csrf_token,templates):
    router=APIRouter(prefix='/crm/retail')
    async def body(request,allowed,required=None):
        csrf(request)
        raw=await request.body()
        if len(raw)>256000:raise HTTPException(413,'Retail request is too large')
        try:value=json.loads(raw)
        except (ValueError,TypeError):raise HTTPException(422,'Provide a JSON object') from None
        if not isinstance(value,dict) or set(value)-set(allowed) or not set(required or [])<=set(value):raise HTTPException(422,'Invalid retail request fields')
        return value
    def filters(q='',category='',issue='',active='',product_type='',vendor='',collection=''):
        return dict(q=q,category=category,issue=issue,active=active,product_type=product_type,vendor=vendor,collection=collection)
    def job_public(row):
        return {'id':row.id,'status':row.status,'total':row.total,'error':row.error,'parent_id':row.parent_id,
            'created_at':row.created_at.isoformat()+'Z','download_available':bool(row.file_ref),'downloaded':bool(row.downloaded_at),'user_confirmed_printed':bool(row.printed_at)}
    def get_job(db,rid):
        row=db.query(RetailPrintJob).filter_by(id=rid,shop=service.shop()).one_or_none()
        if not row:raise HTTPException(404,'Print job not found')
        return row
    def profile(db,value):
        if isinstance(value,str) and value in labels.PRESETS:return labels.PRESETS[value]
        if type(value) is not int:raise HTTPException(422,'Choose a label profile')
        row=db.query(RetailLabelTemplate).filter_by(id=value,shop=service.shop()).one_or_none()
        if not row:raise HTTPException(404,'Label profile not found')
        return row.config
    async def print_payload(request,db,user):
        p=await body(request,{'selection','profile','default_copies','copies','confirm','preview_token'}, {'selection','profile'})
        c=profile(db,p['profile'])
        rows,total=labels.print_rows(db,user,p['selection'],p.get('default_copies',1),p.get('copies'),require_lkr=labels.is_compact(c))
        return p,rows,total,c

    @router.get('/{screen}',include_in_schema=False)
    def screen(screen:str,request:Request,db=Depends(get_db),user=Depends(require_user)):
        permission={'products':'retail.products.view','scanner':'retail.products.view','barcodes':'retail.products.view','labels':'retail.barcodes.print',
                    'audit':'retail.products.audit','sync':'retail.sync.view','barcode-sync':barcode_sync.PERMISSION}.get(screen)
        if not permission:raise HTTPException(404,'Retail screen not found')
        service.require(user,permission)
        return templates.TemplateResponse('retail_scanner.html' if screen=='scanner' else 'retail.html',{'request':request,'user':user,'page_title':{'products':'Product Master','scanner':'Barcode Scanner','barcodes':'Barcode Management','labels':'Barcode Label Printing','audit':'Product Audit','sync':'Import & Sync History','barcode-sync':'Shopify Barcode Sync'}[screen],
            'screen':screen,'is_admin':user.role=='ADMIN','csrf':csrf_token(request),'retail_permissions':[p for p in service.PERMISSIONS if user.role=='ADMIN' or isinstance(user.section_access,list) and p in user.section_access]},headers={'Cache-Control':'private, no-store'})

    def barcode_response(value):
        return JSONResponse(value,headers={'Cache-Control':'private, no-store'})

    @router.get('/api/barcode-sync')
    def barcode_overview(offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        return barcode_response(barcode_sync.overview(db,user,offset))

    @router.get('/api/barcode-sync/reservations')
    def barcode_reservations(offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        barcode_sync.authorize(user);q=barcode_sync.legacy_reservations(db)
        return barcode_response({'total':q.count(),'offset':offset,'rows':[{'variant_id':r.variant_id,'code':r.value,'item_id':r.item_id}
            for r in q.order_by(RetailBarcodeReservation.item_id).offset(offset).limit(100)]})

    @router.post('/api/barcode-sync/retire-legacy')
    async def retire_legacy(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,{'confirmation'},{'confirmation'})
        result=barcode_sync.retire_legacy(db,user,p['confirmation']);db.commit();return barcode_response(result)

    @router.post('/api/barcode-sync/preview')
    async def barcode_preview(request:Request,db=Depends(get_db),user=Depends(require_user)):
        barcode_sync.authorize(user);p=await body(request,{'selection'},{'selection'})
        result=barcode_sync.preview(db,user,p['selection']);db.commit();return barcode_response(result)

    @router.post('/api/barcode-sync/{rid}/approve')
    async def barcode_approve(rid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        barcode_sync.authorize(user);p=await body(request,{'token','confirmation'},{'token','confirmation'})
        result=barcode_sync.approve(db,user,rid,p['token'],p['confirmation']);db.commit();return barcode_response(result)

    @router.post('/api/barcode-sync/{rid}/cancel')
    async def barcode_cancel(rid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        barcode_sync.authorize(user);await body(request,set())
        result=barcode_sync.cancel(db,user,rid);db.commit();return barcode_response(result)

    @router.post('/api/barcode-sync/{rid}/reconcile')
    async def barcode_reconcile(rid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        barcode_sync.authorize(user);await body(request,set())
        result=barcode_sync.reconcile(db,user,rid);db.commit();return barcode_response(result)

    @router.get('/api/barcode-sync/{rid}/events')
    def barcode_events(rid:int,offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        job=barcode_sync.get_job(db,user,rid)
        q=db.query(RetailBarcodeEvent).filter_by(job_id=job.id,shop=service.shop())
        return {'total':q.count(),'rows':[{'action':e.action,'item_id':e.item_id,'actor':e.actor,'detail':e.detail,'at':e.created_at.isoformat()+'Z'} for e in q.order_by(RetailBarcodeEvent.id).offset(offset).limit(50)]}

    @router.get('/api/barcode-sync/{rid}/export.csv')
    def barcode_csv(rid:int,db=Depends(get_db),user=Depends(require_user)):
        return Response(barcode_sync.csv_report(db,user,rid),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="barcode-sync.csv"','Cache-Control':'private, no-store'})

    @router.get('/api/products')
    def products(q:str=Query('',max_length=100),category:str='',issue:str='',active:str='',product_type:str=Query('',max_length=200),vendor:str=Query('',max_length=200),collection:str=Query('',max_length=100),offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.view');return service.page(db,filters(q,category,issue,active,product_type,vendor,collection),offset,limit)

    @router.get('/api/scan')
    def scan(code:str=Query(...,min_length=1,max_length=100),offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.view')
        return barcode_response(service.scan_page(db,code,offset))

    @router.get('/api/scanner/search')
    def scanner_search(mode:str, q:str=Query(...,min_length=1,max_length=100),offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.view');return barcode_response(service.scanner_search(db,mode,q,offset))

    @router.get('/api/scanner/products/{rid}')
    def scanner_details(rid:int,version:int=Query(...,ge=1),db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.view');row=service.scanner_row(db,rid,version)
        return barcode_response({'row':service.public(row,service.audit_context(db))})

    @router.get('/api/scanner/products/{rid}/inventory')
    def scanner_inventory(rid:int,version:int=Query(...,ge=1),db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.view');row=service.scanner_row(db,rid,version)
        expected=(row.version,row.product_id,row.variant_id,row.inventory_item_id,row.product_status,row.missing)
        if row.missing or row.product_status!='ACTIVE':return barcode_response(retail_inventory.unavailable('PRODUCT_INACTIVE'))
        result=retail_inventory.client.lookup({'shop':row.shop,'version':row.version,'product_id':row.product_id,'variant_id':row.variant_id,'inventory_item_id':row.inventory_item_id})
        db.expire_all();current=service.scanner_row(db,rid,version)
        if expected!=(current.version,current.product_id,current.variant_id,current.inventory_item_id,current.product_status,current.missing):raise HTTPException(409,'Catalog changed. Scan again.')
        return barcode_response(result)

    @router.get('/api/scanner/products/{rid}/admin')
    def scanner_admin(rid:int,version:int=Query(...,ge=1),db=Depends(get_db),user=Depends(require_user)):
        from fastapi.responses import RedirectResponse
        import re
        service.require(user,'retail.products.view');row=service.scanner_row(db,rid,version)
        if row.missing or not re.fullmatch(r'gid://shopify/Product/[0-9]+',row.product_id):raise HTTPException(409,'Shopify product identity unavailable')
        return RedirectResponse(f'https://{service.shop()}.myshopify.com/admin/products/{row.product_id.rsplit("/",1)[-1]}',status_code=303,headers={'Cache-Control':'private, no-store'})

    @router.post('/api/scanner/products/{rid}/labels')
    async def scanner_labels(rid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.view');service.require(user,'retail.barcodes.print')
        p=await body(request,{'version'},{'version'})
        if type(p['version']) is not int or p['version']<1:raise HTTPException(422,'Current product version required')
        service.write_lock(db);row=service.scanner_row(db,rid,p['version'])
        if 'STALE' in service.issues(row,service.audit_context(db)):raise HTTPException(409,'Refresh the stale catalog before printing labels')
        selection=service.selection(db,user,{'ids':[rid]})
        labels.print_rows(db,user,selection['token'])  # Reuse existing barcode/conflict/status checks.
        service.audit(db,user,'SCANNER_LABEL_HANDOFF',rid,version=row.version)
        db.commit();return barcode_response(selection)

    @router.get('/api/filter-options')
    def filter_options(db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.view');return service.filter_options(db)

    @router.get('/api/audit')
    def audit_summary(db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.audit');return service.summary(db)

    @router.get('/api/export.csv')
    def export(q:str=Query('',max_length=100),category:str='',issue:str='',active:str='',product_type:str=Query('',max_length=200),vendor:str=Query('',max_length=200),collection:str=Query('',max_length=100),db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.audit');f=filters(q,category,issue,active,product_type,vendor,collection)
        service.filtered(db,f)  # Validate before beginning a streaming response.
        return StreamingResponse(service.csv_export(db,f),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="retail-audit.csv"','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @router.get('/api/products/{rid}/history')
    def history(rid:int,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.view')
        row=db.query(RetailVariant).filter_by(id=rid,shop=service.shop()).one_or_none()
        if not row:raise HTTPException(404,'Variant not found')
        return {'rows':[{'action':r.action,'actor':r.actor,'detail':r.detail,'at':r.created_at.isoformat()+'Z'} for r in db.query(RetailAudit).filter_by(shop=service.shop(),variant_id=rid).order_by(RetailAudit.id.desc()).limit(100)]}

    @router.post('/api/selection')
    async def select_products(request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.view');p=await body(request,{'ids','filters'})
        result=service.selection(db,user,p);db.commit();return result

    @router.get('/api/selection/{token}')
    def selected(token:str,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.view');value=service.preview_record(db,user,token,'SELECTION');ctx=service.audit_context(db)
        q=db.query(RetailVariant).filter(RetailVariant.shop==service.shop(),RetailVariant.id.in_(value.payload['ids']))
        return {'total':q.count(),'rows':[service.public(r,ctx) for r in q.order_by(RetailVariant.id).offset(offset).limit(limit)]}

    @router.post('/api/allocations/preview')
    async def allocation_preview(request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.barcodes.assign');p=await body(request,{'selection','kind','category','reason'},{'selection','kind'})
        result=service.allocate_preview(db,user,p['selection'],p['kind'],p.get('category',''),p.get('reason',''));db.commit();return result

    # No target/actor arguments: identity is fixed and actor comes from the
    # existing authenticated session. These actions are never worker-dispatched.
    @router.get('/api/barcode-p1-pilot')
    def p1_status(db=Depends(get_db),user=Depends(require_user)):
        return JSONResponse(p1_pilot.status(db,user),headers={'Cache-Control':'no-store'})

    @router.post('/api/barcode-p1-pilot/prepare')
    async def p1_prepare(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p1_pilot.authorize(user)
        p=await body(request,{'editors_paused','risk_accepted'},{'editors_paused','risk_accepted'})
        actor=user.id;engine=db.get_bind();db.rollback()
        result=await run_in_threadpool(p1_pilot.prepare,engine,actor,**p)
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.post('/api/barcode-p1-pilot/execute')
    async def p1_execute(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p1_pilot.authorize(user)
        p=await body(request,{'nonce','confirmation','editors_paused','risk_accepted'},{'nonce','confirmation','editors_paused','risk_accepted'})
        actor=user.id;engine=db.get_bind();db.rollback()
        result=await run_in_threadpool(p1_pilot.execute,engine,actor,**p)
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.post('/api/barcode-p1-pilot/reconcile')
    async def p1_reconcile(request:Request,db=Depends(get_db),user=Depends(require_user)):
        p1_pilot.authorize(user);await body(request,set())
        actor=user.id;engine=db.get_bind();db.rollback()
        result=await run_in_threadpool(p1_pilot.reconcile,engine,actor)
        return JSONResponse(result,headers={'Cache-Control':'no-store'})

    @router.get('/api/allocations/{token}')
    def allocation_page(token:str,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=100),db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.barcodes.assign');preview=service.preview_record(db,user,token,'ALLOCATION');rows=preview.payload['rows']
        return {'token':token,'count':len(rows),'rows':rows[offset:offset+limit],'offset':offset,'shopify_write':False}

    @router.post('/api/allocations/commit')
    async def allocation_commit(request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.barcodes.assign');p=await body(request,{'token','confirm'},{'token','confirm'})
        try:result=service.allocate_commit(db,user,p['token'],p['confirm']);db.commit();return result
        except IntegrityError:db.rollback();raise HTTPException(409,'Identifier collision; nothing committed. Preview again.') from None

    @router.post('/api/products/{rid}/category')
    async def category(rid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.manage');p=await body(request,{'category','version','confirm'},{'category','version','confirm'})
        if not isinstance(p['category'],str) or p['category'] not in service.CATEGORIES or type(p['version']) is not int or p['version']<1 or p['confirm'] is not True:raise HTTPException(422,'Choose and confirm the category')
        service.write_lock(db);row=db.query(RetailVariant).filter_by(shop=service.shop(),id=rid,version=p['version']).one_or_none()
        if not row:raise HTTPException(409,'Variant changed or unavailable')
        service.audit(db,user,'CATEGORY_MAPPED',rid,before=row.category,after=p['category']);row.category=p['category'];row.version+=1;db.commit();return {'ok':True}

    @router.post('/api/products/{rid}/retire-local-barcode')
    async def retire_local(rid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        p=await body(request,{'version','local_barcode','shopify_barcode','reason','confirm','labels_ack'},{'version','local_barcode','shopify_barcode','reason','confirm','labels_ack'})
        return service.retire_local_barcode(db,user,rid,p)

    @router.post('/api/products/{rid}/approve-alternative')
    async def approve_alternative(rid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.manage');p=await body(request,{'reason','version','confirm'},{'reason','version','confirm'})
        if not isinstance(p['reason'],str) or not 10<=len(p['reason'].strip())<=500 or type(p['version']) is not int or p['confirm'] is not True:raise HTTPException(422,'Explain and confirm the alternative scan identifier')
        service.write_lock(db);row=db.query(RetailVariant).filter_by(id=rid,shop=service.shop(),version=p['version']).one_or_none()
        if not row or not row.local_barcode or not row.barcode:raise HTTPException(409,'Variant changed or has no alternate local barcode')
        row.barcode_source='LOCAL_ALTERNATIVE';row.barcode_alternative_reason=p['reason'].strip();row.version+=1
        service.audit(db,user,'ALTERNATIVE_APPROVED',rid,shopify_barcode=row.barcode,local_barcode=row.local_barcode,reason=p['reason'].strip());db.commit();return {'ok':True}

    @router.post('/api/exceptions')
    async def exception(request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.manage');p=await body(request,{'kind','value','ids','reason','confirm'},{'kind','value','ids','reason','confirm'})
        if not isinstance(p['kind'],str) or p['kind'] not in {'sku','barcode','effective'} or not isinstance(p['value'],str) or not 1<=len(p['value'])<=500 or not isinstance(p['reason'],str) or not 10<=len(p['reason'].strip())<=500 or p['confirm'] is not True:raise HTTPException(422,'Document why these variants intentionally share an identifier')
        selected=service.selection(db,user,{'ids':p['ids']});ids=service.preview_record(db,user,selected['token'],'SELECTION').payload['ids']
        column=RetailVariant.sku if p['kind']=='sku' else RetailVariant.barcode
        if p['kind']=='effective':
            from sqlalchemy import func
            column=func.coalesce(RetailVariant.local_barcode,RetailVariant.barcode)
        actual={r.id for r in db.query(RetailVariant).filter(RetailVariant.shop==service.shop(),column==p['value'])}
        if actual!=set(ids) or len(ids)<2:raise HTTPException(409,'Exception must include the entire current duplicate group')
        db.add(RetailException(shop=service.shop(),kind=p['kind'],value=p['value'],variant_ids=ids,reason=p['reason'].strip(),actor=user.username))
        service.audit(db,user,'SHARING_EXCEPTION',kind=p['kind'],value=p['value'],ids=ids,reason=p['reason'].strip());db.commit();return {'ok':True}

    @router.post('/api/exceptions/revoke')
    async def revoke_exception(request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.products.manage');p=await body(request,{'kind','value','reason','confirm'},{'kind','value','reason','confirm'})
        if not isinstance(p['kind'],str) or p['kind'] not in {'sku','barcode','effective'} or not isinstance(p['value'],str) or not 1<=len(p['value'])<=500 or not isinstance(p['reason'],str) or not 10<=len(p['reason'].strip())<=480 or p['confirm'] is not True:raise HTTPException(422,'Explain and confirm the sharing revocation')
        service.write_lock(db)
        old=db.query(RetailException).filter_by(shop=service.shop(),kind=p['kind'],value=p['value']).order_by(RetailException.id.desc()).first()
        if not old or not old.variant_ids:raise HTTPException(409,'No active sharing approval found')
        db.add(RetailException(shop=service.shop(),kind=p['kind'],value=p['value'],variant_ids=[],reason='REVOKED: '+p['reason'].strip(),actor=user.username))
        service.audit(db,user,'SHARING_EXCEPTION_REVOKED',kind=p['kind'],value=p['value'],reason=p['reason'].strip());db.commit();return {'ok':True}

    @router.post('/api/sync')
    async def queue_sync(request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.sync.run');p=await body(request,{'mode','confirm'},{'mode','confirm'})
        if p['confirm'] is not True:raise HTTPException(422,'Confirm the read-only catalog import')
        job=sync.queue(db,user,p['mode']);rid=job.id;db.commit();return {'id':rid,'status':'QUEUED'}

    @router.get('/api/sync')
    def sync_history(offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.sync.view');q=db.query(RetailSyncJob).filter_by(shop=service.shop())
        return {'total':q.count(),'rows':[{'id':r.id,'mode':r.mode,'actor':r.actor,'status':r.status,'scanned':r.scanned,'counts':r.counts,'error':r.error,
            'started_at':r.started_at.isoformat()+'Z','heartbeat':r.heartbeat.isoformat()+'Z','completed_at':r.completed_at.isoformat()+'Z' if r.completed_at else None,
            'duration_seconds':round(((r.completed_at or datetime.utcnow())-r.started_at).total_seconds(),1),'error_report_url':f'/crm/retail/api/sync/{r.id}/errors.csv' if r.error else None} for r in q.order_by(RetailSyncJob.id.desc()).offset(offset).limit(50)]}

    @router.get('/api/sync/{rid}/errors.csv')
    def sync_errors(rid:int,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.sync.view');row=db.query(RetailSyncJob).filter_by(id=rid,shop=service.shop()).one_or_none()
        if not row:raise HTTPException(404,'Import not found')
        import csv,io
        out=io.StringIO();writer=csv.writer(out);writer.writerow(['Job','Status','Error','Staged variants']);writer.writerow([row.id,row.status,row.error or '',row.scanned])
        return Response(out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="retail-import-errors.csv"','Cache-Control':'private, no-store'})

    @router.get('/api/profiles')
    def profiles(db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.barcodes.print')
        return {'rows':[{'id':name,'title':labels.PRESET_TITLES.get(name,name+' mm — 203 dpi'),'config':c,'version':0} for name,c in labels.PRESETS.items()]+[
            {'id':r.id,'title':r.title,'config':r.config,'version':r.version} for r in db.query(RetailLabelTemplate).filter_by(shop=service.shop()).order_by(RetailLabelTemplate.id)],
            'max_labels':__import__('settings').settings.RETAIL_MAX_LABELS}

    @router.post('/api/profiles')
    async def save_profile(request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.settings.manage');p=await body(request,{'title','config','confirm'},{'title','config','confirm'})
        if p['confirm'] is not True or not isinstance(p['title'],str) or not 1<=len(p['title'].strip())<=100:raise HTTPException(422,'Name and confirm the new label profile')
        row=RetailLabelTemplate(shop=service.shop(),title=p['title'].strip(),config=labels.validate_config(p['config']));db.add(row);db.flush()
        service.audit(db,user,'LABEL_PROFILE_CREATED',template_id=row.id,config=row.config);rid=row.id;db.commit();return {'id':rid}

    @router.post('/api/labels/preview')
    async def preview_labels(request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.barcodes.print');p,rows,total,c=await print_payload(request,db,user)
        if labels.is_compact(c):labels.validate_rows(rows,c)
        raw=labels.backend.render(labels.labels(rows,limit=5),c)
        preview=RetailPreview(token=secrets.token_hex(16),shop=service.shop(),actor_id=user.id,kind='PRINT',payload={'rows':rows,'total':total,'config':c},expires_at=datetime.utcnow()+timedelta(minutes=10))
        db.add(preview);db.commit()
        return Response(raw,media_type='application/pdf',headers={'X-Retail-Preview':preview.token,'Content-Disposition':'inline; filename="label-preview.pdf"','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @router.post('/api/labels/jobs')
    async def print_job(request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.barcodes.print');p,rows,total,c=await print_payload(request,db,user)
        if p.get('confirm') is not True:raise HTTPException(422,'Review the preview and confirm PDF generation')
        preview=service.preview_record(db,user,p.get('preview_token'),'PRINT')
        if preview.payload!={'rows':rows,'total':total,'config':c}:raise HTTPException(409,'Products, copies or label profile changed after preview. Preview again.')
        row=labels.create_job(db,user,rows,total,c);result=job_public(row);db.commit();return result

    @router.get('/api/labels/jobs')
    def print_history(offset:int=Query(0,ge=0),db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.barcodes.print');q=db.query(RetailPrintJob).filter_by(shop=service.shop())
        return {'total':q.count(),'rows':[job_public(r) for r in q.order_by(RetailPrintJob.id.desc()).offset(offset).limit(50)]}

    @router.get('/api/labels/jobs/{rid}/pdf')
    def download(rid:int,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.barcodes.print');row=get_job(db,rid)
        if row.status!='GENERATED':raise HTTPException(409,'The print document is not available yet')
        labels.ensure_reprint_safe(db,row)
        raw=labels.read_pdf(row);row.downloaded_at=datetime.utcnow();service.audit(db,user,'PRINT_DOCUMENT_DOWNLOADED',job_id=rid);db.commit()
        return Response(raw,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="pixiepinks-labels-{rid}.pdf"','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @router.post('/api/labels/jobs/{rid}/printed')
    async def mark_printed(rid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.barcodes.print');p=await body(request,{'confirm'},{'confirm'});row=get_job(db,rid)
        if row.status!='GENERATED' or p['confirm'] is not True:raise HTTPException(409,'Confirm only after physically printing and inspecting the labels')
        row.printed_at=datetime.utcnow();service.audit(db,user,'USER_CONFIRMED_PRINTED',job_id=rid);db.commit();return {'ok':True}

    @router.post('/api/labels/jobs/{rid}/reprint')
    async def reprint(rid:int,request:Request,db=Depends(get_db),user=Depends(require_user)):
        service.require(user,'retail.barcodes.print');p=await body(request,{'confirm'},{'confirm'});original=get_job(db,rid)
        if p['confirm'] is not True:raise HTTPException(422,'Confirm a new PDF from the retained snapshot')
        labels.ensure_reprint_safe(db,original)
        job=labels.create_job(db,user,original.rows,original.total,original.template,parent=rid);result=job_public(job);db.commit();return result
    return router
