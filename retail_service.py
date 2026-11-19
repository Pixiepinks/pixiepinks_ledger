"""Shop-scoped retail reads, explicit local allocations and immutable previews."""
import csv
import io
import re
import secrets
from collections import Counter
from datetime import datetime,timedelta
from fastapi import HTTPException
from sqlalchemy import select,or_,func,case
from retail_models import *
from settings import settings
from retail_barcode_contract import RELEASE_ID

PERMISSIONS=('retail.barcodes.shopify_sync','retail.products.view','retail.products.audit','retail.products.manage','retail.barcodes.assign',
             'retail.barcodes.print','retail.sync.run','retail.sync.view','retail.settings.manage')
CATEGORIES={'BIC':'Bicycle','GFT':'Gift Pack','TOY':'Toy','BAG':'School Bag','BOT':'Water Bottle',
            'PLT':'Plate Set','RID':'Ride-on Vehicle','STA':'Stationery','OTH':'Other'}


def shop():
    value=(settings.SHOPIFY_SHOP or '').strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}',value):raise HTTPException(409,'Configure the Shopify shop before using Retail Management')
    return value


def require(user,permission):
    if not settings.RETAIL_ENABLED:raise HTTPException(404,'Retail Management is disabled')
    if not user or not user.is_active or not (user.role=='ADMIN' or isinstance(user.section_access,list) and permission in user.section_access):
        raise HTTPException(403,'Retail permission required: '+permission)


def write_lock(db):
    connection=db.connection()
    if connection.dialect.name=='sqlite' and not connection.connection.driver_connection.in_transaction:connection.exec_driver_sql('BEGIN IMMEDIATE')
    row=db.execute(select(RetailShop).where(RetailShop.shop==shop()).with_for_update()).scalar_one_or_none()
    if row is None:
        # Serialize first-use shop creation with an upsert, including PostgreSQL.
        from crm_analytics_tracking import insert_for
        db.execute(insert_for(connection,RetailShop.__table__).values(shop=shop()).on_conflict_do_nothing(index_elements=['shop']))
        row=db.execute(select(RetailShop).where(RetailShop.shop==shop()).with_for_update()).scalar_one()
    return row


def audit(db,user,action,variant=None,**detail):
    db.add(RetailAudit(shop=shop(),actor=user.username if hasattr(user,'username') else str(user),action=action,variant_id=variant,detail=detail))


def code_valid(value):
    return isinstance(value,str) and bool(value.strip()) and len(value)<=100 and all(32<=ord(c)<=126 for c in value)


def scan_value(row):return row.local_barcode or row.barcode or ''
def sku_value(row):return row.sku or row.local_sku or ''


def audit_context(db):
    rows=db.query(RetailVariant.id,RetailVariant.product_id,RetailVariant.sku,RetailVariant.barcode,RetailVariant.local_barcode,RetailVariant.local_sku).filter_by(shop=shop()).all()
    skus=Counter(r.sku for r in rows if r.sku);barcodes=Counter(r.barcode for r in rows if r.barcode)
    effective=Counter(r.local_barcode or r.barcode for r in rows if r.local_barcode or r.barcode)
    products=Counter(r.product_id for r in rows)
    exceptions={(r.kind,r.value):set(r.variant_ids) for r in db.query(RetailException).filter_by(shop=shop()).order_by(RetailException.id).all()}
    groups={}
    for row in rows:
        for kind,value in [('sku',row.sku),('barcode',row.barcode),('effective',row.local_barcode or row.barcode)]:
            if value:groups.setdefault((kind,value),set()).add(row.id)
    shared={key for key,ids in groups.items() if ids==exceptions.get(key) and len(ids)>1}
    checks={}
    for item in db.query(RetailBarcodeItem).join(RetailBarcodeJob).filter(RetailBarcodeJob.shop==shop(),RetailBarcodeItem.checked_at!=None).order_by(RetailBarcodeItem.checked_at.desc(),RetailBarcodeItem.id.desc()):
        checks.setdefault(item.variant_id,{'status':item.status,'code':item.snapshot['code'],'checked_at':item.checked_at.isoformat()+'Z','verified_at':item.verified_at.isoformat()+'Z' if item.verified_at else None})
    return {'barcode_checks':checks,'sku':skus,'barcode':barcodes,'effective':effective,'products':products,'shared':shared,'total':len(rows)}


def issues(row,ctx):
    result=[]
    if not row.sku:result.append('MISSING_SKU')
    elif not row.sku.strip() or any(ord(c)<32 for c in row.sku):result.append('INVALID_SKU')
    if not row.barcode:result.append('MISSING_BARCODE')
    for kind in ['sku','barcode']:
        value=getattr(row,kind)
        if value and ctx[kind][value]>1:result.append(('SHARED_' if (kind,value) in ctx['shared'] else 'DUPLICATE_')+kind.upper())
    value=scan_value(row)
    if value and not code_valid(value):result.append('INVALID_BARCODE')
    if row.barcode and code_valid(row.barcode) and not row.local_barcode:result.append('EXISTING_MANUFACTURER_OR_EXTERNAL')
    if value and ctx['effective'][value]>1 and ('effective',value) not in ctx['shared']:result.append('SCAN_CONFLICT')
    check=ctx.get('barcode_checks',{}).get(row.id,{})
    verified=check.get('status') in {'VERIFIED','PILOT_VERIFIED'} and check.get('code')==row.local_barcode and (not row.barcode or row.barcode==row.local_barcode)
    if row.local_sku or row.local_barcode and not verified:result.append('LOCAL_PENDING_SYNC')
    if verified:result.append('VERIFIED_ON_SHOPIFY')
    if row.local_barcode and row.barcode and row.local_barcode==row.barcode:result.append('LOCAL_MATCHES_SHOPIFY')  # Observation, never a confirmed write.
    if row.local_barcode and row.barcode and row.local_barcode!=row.barcode and row.barcode_source!='LOCAL_ALTERNATIVE':result.append('BARCODE_RECONCILIATION')
    if row.local_sku and row.sku and row.local_sku!=row.sku:result.append('SKU_RECONCILIATION')
    if ctx['products'][row.product_id]>1:
        result.append('MULTIPLE_VARIANTS')
        if not row.options or all(o.get('value') in {'','Default Title'} for o in row.options):result.append('MISSING_OPTIONS')
    if row.stale or row.synced_at<datetime.utcnow()-timedelta(days=7):result.append('STALE')
    if row.missing:result.append('MISSING_IN_SHOPIFY')
    if any(x in result for x in ['INVALID_BARCODE','INVALID_SKU','BARCODE_RECONCILIATION','DUPLICATE_BARCODE','DUPLICATE_SKU','SCAN_CONFLICT','MISSING_OPTIONS','SKU_RECONCILIATION']):result.append('MANUAL_REVIEW')
    return result


def public(row,ctx):
    return {'id':row.id,'version':row.version,'product_id':row.product_id,'variant_id':row.variant_id,'title':row.title,'variant_title':row.variant_title,
        'sku':row.sku or '', 'local_sku':row.local_sku or '', 'barcode':row.barcode or '', 'local_barcode':row.local_barcode or '',
        'shopify_verification':ctx.get('barcode_checks',{}).get(row.id),
        'effective_barcode':scan_value(row),'category':row.category or '', 'product_type':row.product_type,'vendor':row.vendor,
        'price':str(row.price) if row.price is not None else '', 'currency':row.currency,'image':row.image,
        'compare_at_price':str(row.compare_at_price) if row.compare_at_price is not None else '', 'inventory_item_id':row.inventory_item_id,'inventory_tracked':row.tracked,
        'shopify_updated_at':row.shopify_updated_at.isoformat()+'Z' if row.shopify_updated_at else None,'barcode_source':row.barcode_source,'alternative_reason':row.barcode_alternative_reason,'print_symbology':'CODE128','external_symbology':'UNKNOWN',
        'status':row.product_status,'missing':row.missing,'options':row.options,'collections':row.collections,
        'collections_complete':row.collections_complete,'synced_at':row.synced_at.isoformat()+'Z','issues':issues(row,ctx),
        'identifier_source':'LOCAL_VERIFIED_SHOPIFY' if 'VERIFIED_ON_SHOPIFY' in issues(row,ctx) else 'LOCAL_PENDING_SYNC' if row.local_barcode else 'SHOPIFY_EXISTING' if row.barcode else 'MISSING',
        'admin_url':f"https://{row.shop}.myshopify.com/admin/products/{row.product_id.rsplit('/',1)[-1]}"}


def scan_page(db,code,offset=0):
    """One exact, indexed barcode matcher used by both scanner interfaces."""
    if not code_valid(code):raise HTTPException(422,'Scan a valid barcode without control characters')
    query=db.query(RetailVariant).filter(RetailVariant.shop==shop(),or_(RetailVariant.barcode==code,RetailVariant.local_barcode==code))
    ctx=audit_context(db)
    return {'code':code,'total':query.count(),'rows':[public(row,ctx) for row in query.order_by(RetailVariant.id).offset(offset).limit(50)]}


def scanner_search(db,mode,term,offset=0):
    if mode not in {'sku','name'} or not isinstance(term,str) or not term.strip() or len(term)>100 or any(ord(c)<32 or ord(c)==127 for c in term):raise HTTPException(422,'Enter a valid SKU or product name, up to 100 characters')
    query=db.query(RetailVariant).filter_by(shop=shop())
    if mode=='sku':query=query.filter(or_(RetailVariant.sku==term,RetailVariant.local_sku==term))
    else:
        for word in term.split():
            escaped=word.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            query=query.filter(or_(RetailVariant.title.ilike('%'+escaped+'%',escape='\\'),RetailVariant.variant_title.ilike('%'+escaped+'%',escape='\\')))
    ctx=audit_context(db)
    return {'mode':mode,'query':term,'total':query.count(),'rows':[public(row,ctx) for row in query.order_by(RetailVariant.id).offset(offset).limit(50)]}


def scanner_row(db,rid,version):
    row=db.query(RetailVariant).filter_by(shop=shop(),id=rid).populate_existing().one_or_none()
    if not row:raise HTTPException(404,'Product not found in this store')
    if row.version!=version:raise HTTPException(409,'Catalog changed. Scan or search for this product again.')
    return row


def filtered(db,filters):
    allowed={'q','category','issue','active','product_type','vendor','collection'}
    if not isinstance(filters,dict) or set(filters)-allowed:raise HTTPException(422,'Unknown product filter')
    q=db.query(RetailVariant).filter_by(shop=shop())
    term=filters.get('q','')
    if not isinstance(term,str) or len(term)>100:raise HTTPException(422,'Search must be at most 100 characters')
    for word in term.split():
        escaped=word.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        q=q.filter(or_(*[getattr(RetailVariant,key).ilike('%'+escaped+'%',escape='\\') for key in ['title','variant_title','sku','local_sku','barcode','local_barcode','product_type','vendor']]))
    category=filters.get('category','')
    if not isinstance(category,str):raise HTTPException(422,'Invalid category')
    if category:
        if category=='__unmapped__':q=q.filter(or_(RetailVariant.category==None,RetailVariant.category==''))
        else:
            if category not in CATEGORIES:raise HTTPException(422,'Choose an explicit category')
            q=q.filter_by(category=category)
    for key in ['product_type','vendor']:
        value=filters.get(key,'')
        if not isinstance(value,str) or len(value)>200:raise HTTPException(422,'Invalid Shopify '+key.replace('_',' '))
        if value:q=q.filter(getattr(RetailVariant,key)==value)
    collection=filters.get('collection','')
    if not isinstance(collection,str) or (collection and not re.fullmatch(r'gid://shopify/Collection/[0-9]{1,30}',collection)):raise HTTPException(422,'Choose an imported Shopify collection')
    if collection:
        ids=[row.id for row in q.yield_per(250) if any(item.get('id')==collection for item in row.collections)]
        q=q.filter(RetailVariant.id.in_(ids))
    active=filters.get('active','')
    if not isinstance(active,str) or active not in {'','active','inactive'}:raise HTTPException(422,'Invalid product status filter')
    if active=='active':q=q.filter_by(product_status='ACTIVE',missing=False)
    if active=='inactive':q=q.filter(or_(RetailVariant.product_status!='ACTIVE',RetailVariant.missing==True))
    issue=filters.get('issue','')
    if not isinstance(issue,str):raise HTTPException(422,'Invalid audit filter')
    if issue:
        known={'INVALID_SKU','BARCODE_RECONCILIATION','MISSING_SKU','MISSING_BARCODE','DUPLICATE_SKU','DUPLICATE_BARCODE','INVALID_BARCODE','SCAN_CONFLICT','LOCAL_PENDING_SYNC','STALE','MISSING_IN_SHOPIFY','MULTIPLE_VARIANTS','MISSING_OPTIONS','MANUAL_REVIEW','SHARED_SKU','SHARED_BARCODE','SKU_RECONCILIATION','EXISTING_MANUFACTURER_OR_EXTERNAL'}
        if issue not in known:raise HTTPException(422,'Unknown audit filter')
        ctx=audit_context(db)
        ids=[row.id for row in q.yield_per(250) if issue in issues(row,ctx)]
        q=q.filter(RetailVariant.id.in_(ids))
    return q


def page(db,filters,offset=0,limit=50):
    query=filtered(db,filters);ctx=audit_context(db)
    term=filters.get('q','').strip()
    if term:
        exact=or_(*[getattr(RetailVariant,key)==term for key in ['sku','local_sku','barcode','local_barcode']])
        query=query.order_by(case((exact,0),else_=1))
    return {'total':query.count(),'rows':[public(row,ctx) for row in query.order_by(RetailVariant.id).offset(offset).limit(limit)],'categories':CATEGORIES}


def filter_options(db):
    query=db.query(RetailVariant).filter_by(shop=shop())
    result={'total':query.count(),'categories':[], 'product_types':[], 'vendors':[], 'collections':[],'collections_incomplete':False}
    counts=dict(db.query(RetailVariant.category,func.count()).filter(RetailVariant.shop==shop()).group_by(RetailVariant.category))
    result['categories']=[{'value':'__unmapped__','label':'Unmapped','count':counts.get(None,0)+counts.get('',0)}]+[{'value':key,'label':name,'count':counts.get(key,0)} for key,name in CATEGORIES.items()]
    for key,out in [('product_type','product_types'),('vendor','vendors')]:
        values=db.query(getattr(RetailVariant,key),func.count()).filter(RetailVariant.shop==shop(),getattr(RetailVariant,key)!=None,getattr(RetailVariant,key)!='').group_by(getattr(RetailVariant,key)).order_by(getattr(RetailVariant,key)).all()
        result[out]=[{'value':value,'label':value,'count':count} for value,count in values]
    collections={}
    for values,complete in query.with_entities(RetailVariant.collections,RetailVariant.collections_complete).yield_per(250):
        if not complete:result['collections_incomplete']=True
        for entry in {item['id']:item for item in values if isinstance(item,dict) and isinstance(item.get('id'),str) and re.fullmatch(r'gid://shopify/Collection/[0-9]{1,30}',item['id'])}.values():
            key=entry['id']
            if key not in collections:collections[key]={'value':key,'label':entry.get('title') or key,'count':0}
            collections[key]['count']+=1
    result['collections']=sorted(collections.values(),key=lambda item:(item['label'].casefold(),item['value']))
    return result


def summary(db):
    ctx=audit_context(db);q=db.query(RetailVariant).filter_by(shop=shop());counts=Counter()
    for row in q.yield_per(250):counts.update(issues(row,ctx))
    state=db.get(RetailShop,shop())
    return {'products':len(ctx['products']),'variants':ctx['total'],'with_sku':q.filter(RetailVariant.sku!=None,RetailVariant.sku!='').count(),
        'with_barcode':q.filter(RetailVariant.barcode!=None,RetailVariant.barcode!='').count(),
        'duplicate_sku_groups':sum(n>1 for n in ctx['sku'].values()),'duplicate_barcode_groups':sum(n>1 for n in ctx['barcode'].values()),
        'counts':dict(counts),'last_success':state.last_success.isoformat()+'Z' if state and state.last_success else None}


def csv_export(db,filters):
    ctx=audit_context(db);out=io.StringIO();writer=csv.writer(out)
    writer.writerow(['Internal ID','Product','Variant','Shopify variant','Shopify SKU','Local SKU','Shopify barcode','Local barcode','Effective barcode','Issues','Vendor','Product type','Options','Collections','Collections complete','Price','Compare-at price','Currency','Inventory item','Tracked','Shopify updated','Local sync'])
    for row in filtered(db,filters).order_by(RetailVariant.id).yield_per(250):
        values=[row.id,row.title,row.variant_title,row.variant_id,row.sku,row.local_sku,row.barcode,row.local_barcode,scan_value(row),'; '.join(issues(row,ctx)),row.vendor,row.product_type,str(row.options),str(row.collections),row.collections_complete,str(row.price),str(row.compare_at_price),row.currency,row.inventory_item_id,row.tracked,row.shopify_updated_at,row.synced_at]
        writer.writerow([("'"+str(v)) if str(v or '').lstrip(' \t\r\n\ufeff').startswith(('=','+','-','@','\t','\r')) else v for v in values])
        if out.tell()>32000:yield out.getvalue();out.seek(0);out.truncate(0)
    yield out.getvalue()


def selection(db,user,payload):
    if not isinstance(payload,dict) or set(payload) not in [{'ids'},{'filters'}]:raise HTTPException(422,'Choose products or select all filtered results')
    if 'ids' in payload:
        ids=payload['ids']
        if not isinstance(ids,list) or not ids or len(ids)>10000 or any(type(i) is not int or i<1 for i in ids):raise HTTPException(422,'Select 1–10000 variants')
        ids=sorted(set(ids));found=[r.id for r in db.query(RetailVariant.id).filter(RetailVariant.shop==shop(),RetailVariant.id.in_(ids)).all()]
        if sorted(found)!=ids:raise HTTPException(404,'Selection includes unavailable variants')
    else:
        ids=[r.id for r in filtered(db,payload['filters']).with_entities(RetailVariant.id).order_by(RetailVariant.id).limit(10001).all()]
        if not 1<=len(ids)<=10000:raise HTTPException(422,'Select 1–10000 variants; narrow the filter')
    value=RetailPreview(token=secrets.token_hex(16),shop=shop(),actor_id=user.id,kind='SELECTION',payload={'ids':ids,'barcode_release':RELEASE_ID},expires_at=datetime.utcnow()+timedelta(hours=2))
    db.add(value);db.flush();return {'token':value.token,'count':len(ids)}


def preview_record(db,user,token,kind):
    if not isinstance(token,str) or not re.fullmatch('[a-f0-9]{32}',token):raise HTTPException(422,'Invalid preview reference')
    value=db.query(RetailPreview).filter_by(token=token,shop=shop(),actor_id=user.id,kind=kind).one_or_none()
    if not value:raise HTTPException(404,'Preview not found')
    if not value.committed and value.expires_at<datetime.utcnow():raise HTTPException(409,'Preview expired. Select and preview again.')
    return value


def base36(number):
    text=''
    while number:number,r=divmod(number,36);text='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'[r]+text
    return text or '0'


def allocate_preview(db,user,token,kind,category='',reason=''):
    if not isinstance(kind,str) or kind not in {'sku','barcode'}:raise HTTPException(422,'Choose SKU or barcode allocation')
    if not isinstance(category,str) or (category and category not in CATEGORIES):raise HTTPException(422,'Choose an explicit category')
    if kind=='sku' and (not isinstance(category,str) or category not in CATEGORIES):raise HTTPException(422,'Choose the category explicitly')
    if not isinstance(reason,str) or len(reason)>500 or (reason.strip() and len(reason.strip())<10):raise HTTPException(422,'Alternative reason must be at most 500 characters')
    write_lock(db);selection=preview_record(db,user,token,'SELECTION')
    counter=db.get(RetailCounter,(shop(),kind))
    if counter is None:counter=RetailCounter(shop=shop(),kind=kind,value=0);db.add(counter);db.flush()
    start=counter.value;number=start;assignments=[]
    column=RetailVariant.sku if kind=='sku' else RetailVariant.barcode
    existing=set(v[0] for v in db.query(column).filter(RetailVariant.shop==shop(),column!=None).all())
    existing.update(v[0] for v in db.query(RetailIdentifier.value).filter_by(shop=shop(),kind=kind).all())
    ctx=audit_context(db)
    rows=db.query(RetailVariant).filter(RetailVariant.shop==shop(),RetailVariant.id.in_(selection.payload['ids'])).order_by(RetailVariant.id).all()
    for row in rows:
        if row.missing or row.product_status!='ACTIVE':raise HTTPException(409,'Only active, reconciled variants can receive identifiers')
        if getattr(row,'local_'+kind):continue
        original=getattr(row,kind)
        if original:
            if kind=='sku':continue
            if not reason.strip():continue  # Preserve existing barcode by default.
        elif any(v in issues(row,ctx) for v in ['DUPLICATE_SKU','DUPLICATE_BARCODE','SCAN_CONFLICT']):
            raise HTTPException(409,'Resolve legacy identifier conflicts before automatic allocation')
        while True:
            number+=1
            if number>2000000000:raise HTTPException(409,'Identifier sequence exhausted')
            value=f'PP-{category}-{number:06d}' if kind=='sku' else 'P'+base36(number)
            if value not in existing:break
        existing.add(value)
        assignments.append({'id':row.id,'version':row.version,'before':original or '', 'value':value,'title':row.title,'variant':row.variant_title})
    if not assignments:raise HTTPException(409,'No eligible missing identifiers. Existing identifiers are preserved; an alternative barcode requires a reason.')
    result=RetailPreview(token=secrets.token_hex(16),shop=shop(),actor_id=user.id,kind='ALLOCATION',
        payload={'kind':kind,'category':category,'reason':reason.strip(),'start':start,'end':number,'rows':assignments},expires_at=datetime.utcnow()+timedelta(minutes=10))
    db.add(result);db.flush();return {'token':result.token,'count':len(assignments),'rows':assignments[:100],'preview_truncated':len(assignments)>100,'shopify_write':False}


def allocate_commit(db,user,token,confirm):
    if confirm is not True:raise HTTPException(422,'Explicit confirmation is required')
    write_lock(db);preview=preview_record(db,user,token,'ALLOCATION')
    if preview.committed:return {'count':len(preview.payload['rows']),'committed':True,'shopify_write':False}
    p=preview.payload;kind=p['kind'];counter=db.get(RetailCounter,(shop(),kind))
    if counter.value!=p['start']:raise HTTPException(409,'Allocation sequence changed. Preview again.')
    for proposed in p['rows']:
        row=db.query(RetailVariant).filter_by(id=proposed['id'],shop=shop()).one_or_none()
        if row is None or row.version!=proposed['version'] or getattr(row,'local_'+kind):raise HTTPException(409,'Products changed after preview. Preview again.')
        column=RetailVariant.sku if kind=='sku' else RetailVariant.barcode
        if db.query(RetailVariant.id).filter(RetailVariant.shop==shop(),column==proposed['value']).first():raise HTTPException(409,'Identifier appeared in Shopify after preview. Reconcile and preview again.')
        setattr(row,'local_'+kind,proposed['value']);row.version+=1;row.updated_at=datetime.utcnow()
        if kind=='barcode':row.barcode_source='LOCAL_ALTERNATIVE' if row.barcode else 'LOCAL';row.barcode_alternative_reason=p['reason'] or None
        if p['category']:row.category=p['category']
        db.add(RetailIdentifier(shop=shop(),variant_id=row.id,kind=kind,value=proposed['value'],actor=user.username))
        audit(db,user,'IDENTIFIER_ALLOCATED',row.id,kind=kind,before=proposed['before'],after=proposed['value'],reason=p['reason'],shopify_write=False)
    counter.value=p['end'];preview.committed=True
    db.flush();return {'count':len(p['rows']),'committed':True,'shopify_write':False}
