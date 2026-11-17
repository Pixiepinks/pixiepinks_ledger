"""Read-only paginated Shopify import. Stage/checkpoint first, publish atomically."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
import re
import time
import httpx
from fastapi import HTTPException
from sqlalchemy import select,exists
from shopify_catalog_service import ShopifyCatalogClient,ShopifyCatalogError
from retail_models import RetailShop,RetailVariant,RetailSyncJob,RetailImportRow
from settings import settings
import retail_service as service

QUERY='''query RetailVariants($after:String,$query:String) {
 shop { currencyCode }
 productVariants(first:50, after:$after, query:$query, sortKey:ID) {
  pageInfo { hasNextPage endCursor }
  nodes { id title sku barcode price compareAtPrice updatedAt
   selectedOptions { name value } image { url }
   inventoryItem { id tracked }
   product { id title status vendor productType updatedAt featuredImage { url }
    collections(first:5) { nodes { id title } pageInfo { hasNextPage } }
   }
  }
 }
}'''

class RetailCatalogClient(ShopifyCatalogClient):
    def read_page(self,cursor,query,sleep=time.sleep):
        # Exact fixed read-only document: never accept a caller-supplied GraphQL operation.
        endpoint=f'{self.base_url}/admin/api/{settings.SHOPIFY_API_VERSION}/graphql.json'
        for attempt in range(4):
            try:
                response=self.http.post(endpoint,headers={'X-Shopify-Access-Token':self.get_token()},
                    json={'query':QUERY,'variables':{'after':cursor,'query':query}},timeout=httpx.Timeout(30,connect=5))
                if response.status_code==401 and attempt==0:self.clear_token();continue
                payload=response.json() if response.status_code==200 else {}
                throttled=response.status_code==429 or any(e.get('extensions',{}).get('code')=='THROTTLED' for e in payload.get('errors',[]))
                if throttled or response.status_code>=500:
                    if attempt<3:
                        try:delay=float(response.headers.get('Retry-After',2**attempt))
                        except (ValueError,TypeError):delay=2**attempt
                        if delay>30:raise ShopifyCatalogError('Shopify rate limit requires a later retry')
                        sleep(max(0,delay));continue
                if response.status_code!=200 or payload.get('errors') or not isinstance(payload.get('data'),dict):raise ShopifyCatalogError('Shopify retail read failed')
                cost=payload.get('extensions',{}).get('cost',{}).get('throttleStatus',{})
                requested=payload.get('extensions',{}).get('cost',{}).get('requestedQueryCost',100)
                if cost.get('restoreRate',0)>0:
                    wait=(requested-cost.get('currentlyAvailable',requested))/cost['restoreRate']
                    if wait>0:sleep(min(30,wait))
                return payload['data']
            except (httpx.RequestError,ValueError,TypeError):
                if attempt<3:sleep(2**attempt);continue
                raise ShopifyCatalogError('Shopify retail read unavailable') from None
        raise ShopifyCatalogError('Shopify retail read failed')

client=RetailCatalogClient()


def moment(value):
    if not isinstance(value,str):raise ValueError('Missing Shopify update timestamp')
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc).replace(tzinfo=None)


def money(value):
    if value is None:return None
    result=Decimal(value)
    if not result.is_finite() or abs(result)>Decimal('9999999999999999'):raise ValueError('Invalid price')
    return str(result)


def normalize(node,currency):
    product=node['product'];inventory=node.get('inventoryItem') or {};collections=product.get('collections') or {}
    if not re.fullmatch(r'gid://shopify/ProductVariant/[0-9]+',node['id']) or not re.fullmatch(r'gid://shopify/Product/[0-9]+',product['id']):raise ValueError('Invalid identity')
    def text(value,limit):
        if value is not None and (not isinstance(value,str) or len(value)>limit):raise ValueError('Invalid catalogue field')
        return value
    image=(node.get('image') or product.get('featuredImage') or {}).get('url')
    if image and not re.fullmatch(r'https://cdn\.shopify\.com/[^\s]+',image):image=None
    return {'variant_id':node['id'],'product_id':product['id'],'inventory_item_id':inventory.get('id'),
        'title':text(product['title'],500),'variant_title':text(node['title'],500),'sku':text(node.get('sku'),500),
        'barcode':text(node.get('barcode'),500),'product_status':product['status'],'product_type':text(product.get('productType'),200),
        'vendor':text(product.get('vendor'),200),'price':money(node.get('price')),'compare_at_price':money(node.get('compareAtPrice')),
        'currency':currency,'image':image,'tracked':inventory.get('tracked'),'options':node.get('selectedOptions') or [],
        'collections':collections.get('nodes') or [],'collections_complete':not collections.get('pageInfo',{}).get('hasNextPage',False),
        'shopify_updated_at':max(moment(node['updatedAt']),moment(product['updatedAt'])).isoformat()}


def queue(db,user,mode):
    if not isinstance(mode,str) or mode not in {'FULL','INCREMENTAL'}:raise HTTPException(422,'Choose full or incremental sync')
    state=service.write_lock(db)
    if state.sync_job_id:raise HTTPException(409,'A catalog import is already queued/running for this shop')
    job=RetailSyncJob(shop=service.shop(),actor=user.username,mode=mode,status='QUEUED')
    db.add(job);db.flush();state.sync_job_id=job.id
    service.audit(db,user,'SYNC_QUEUED',job_id=job.id,mode=mode)
    return job


def publish(db,job):
    state=service.write_lock(db)
    if state.sync_job_id!=job.id:raise RuntimeError('Import ownership changed')
    staged=db.query(RetailImportRow).filter_by(job_id=job.id)
    total=staged.count()
    if total==0 and job.mode=='FULL' and db.query(RetailVariant).filter_by(shop=job.shop).first():raise ValueError('Empty replacement needs review')
    counts={'created':0,'updated':0,'skipped':0,'failed':0,'missing':0,'conflicts':0,'variants':total}
    existing={r.variant_id:r for r in db.query(RetailVariant).filter_by(shop=job.shop).all()}
    product_ids=set()
    for staged_row in staged.order_by(RetailImportRow.id).yield_per(250):
        data=dict(staged_row.data);data['shopify_updated_at']=datetime.fromisoformat(data['shopify_updated_at']);product_ids.add(data['product_id'])
        row=existing.get(data['variant_id'])
        if row and row.shopify_updated_at and data['shopify_updated_at']<row.shopify_updated_at:counts['skipped']+=1;continue
        if row is None:row=RetailVariant(shop=job.shop,**data);db.add(row);counts['created']+=1
        else:
            for key,value in data.items():setattr(row,key,value)
            row.version+=1;counts['updated']+=1
        row.synced_at=datetime.utcnow();row.updated_at=datetime.utcnow();row.missing=False
        row.stale=data['shopify_updated_at']>job.started_at
        if row.local_barcode and row.barcode and row.local_barcode!=row.barcode and row.barcode_source!='LOCAL_ALTERNATIVE':counts['conflicts']+=1
    db.flush()
    if job.mode=='FULL':
        seen=exists(select(RetailImportRow.id).where(RetailImportRow.job_id==job.id,RetailImportRow.variant_id==RetailVariant.variant_id))
        counts['missing']=db.query(RetailVariant).filter(RetailVariant.shop==job.shop,RetailVariant.synced_at<job.started_at,~seen).update(
            {'missing':True,'stale':True,'version':RetailVariant.version+1},synchronize_session=False)
    ctx=service.audit_context(db)
    imported_ids=select(RetailImportRow.variant_id).where(RetailImportRow.job_id==job.id)
    counts['conflicts']=sum(any(flag in service.issues(row,ctx) for flag in ['DUPLICATE_SKU','DUPLICATE_BARCODE','SCAN_CONFLICT','BARCODE_RECONCILIATION','SKU_RECONCILIATION']) for row in db.query(RetailVariant).filter(RetailVariant.shop==job.shop,RetailVariant.variant_id.in_(imported_ids)).yield_per(250))
    counts['products']=len(product_ids);job.counts=counts;job.status='COMPLETE';job.completed_at=datetime.utcnow()
    state.last_success=job.completed_at;state.watermark=job.started_at;state.sync_job_id=None
    service.audit(db,job.actor,'SYNC_COMPLETE',job_id=job.id,**counts)


def run_sync(sessions,rid,reader=None):
    reader=reader or client
    try:
        with sessions() as db:
            job=db.get(RetailSyncJob,rid);cursor=job.cursor;state=db.get(RetailShop,job.shop)
            query='updated_at:>='+ (state.watermark-timedelta(minutes=5)).isoformat()+'Z' if job.mode=='INCREMENTAL' and state.watermark else None
        seen_cursors=set()
        while True:
            data=reader.read_page(cursor,query)
            connection=data['productVariants'];page=connection['pageInfo'];nodes=connection['nodes'];currency=data['shop']['currencyCode']
            if not isinstance(nodes,list) or len(nodes)>50 or not re.fullmatch('[A-Z]{3}',currency):raise ValueError('Invalid page')
            with sessions() as db:
                job=db.get(RetailSyncJob,rid)
                if job.status!='RUNNING':return
                for node in nodes:
                    normalized=normalize(node,currency)
                    old=db.query(RetailImportRow).filter_by(job_id=rid,variant_id=normalized['variant_id']).one_or_none()
                    if old:old.data=normalized
                    else:db.add(RetailImportRow(job_id=rid,variant_id=normalized['variant_id'],data=normalized))
                job.scanned+=len(nodes);job.cursor=page['endCursor'];job.heartbeat=datetime.utcnow()
                if job.scanned>settings.RETAIL_MAX_VARIANTS:raise ValueError('Catalog safety limit exceeded')
                db.commit()
            if not page['hasNextPage']:break
            cursor=page['endCursor']
            if not isinstance(cursor,str) or not cursor or cursor in seen_cursors:raise ValueError('Invalid pagination')
            seen_cursors.add(cursor)
        with sessions() as db:
            job=db.get(RetailSyncJob,rid)
            if job.status=='RUNNING':publish(db,job);db.commit()
    except Exception:
        # No response bodies, tokens, URLs or customer payloads in diagnostic output.
        with sessions() as db:
            job=db.get(RetailSyncJob,rid)
            if job and job.status=='RUNNING':
                state=service.write_lock(db);job.status='FAILED';job.error='Catalog import failed; staged pages retained. Live local master unchanged. Start a new read-only sync.'
                job.completed_at=datetime.utcnow();job.counts={'created':0,'updated':0,'skipped':0,'conflicts':0,'failed':1,'staged_variants':job.scanned}
                if state.sync_job_id==rid:state.sync_job_id=None
                service.audit(db,job.actor,'SYNC_FAILED',job_id=rid);db.commit()


def dispatch_one(sessions):
    with sessions() as db:
        # Reclaim only abandoned reads. Label send jobs never touch USB or the network.
        service.write_lock(db)
        stale=db.query(RetailSyncJob).filter(RetailSyncJob.shop==service.shop(),RetailSyncJob.status=='RUNNING',RetailSyncJob.heartbeat<datetime.utcnow()-timedelta(minutes=15)).all()
        for job in stale:
            job.status='FAILED';job.error='Import interrupted; master unchanged. Start a new sync.';job.completed_at=datetime.utcnow()
            state=db.get(RetailShop,job.shop)
            if state.sync_job_id==job.id:state.sync_job_id=None
        job=db.execute(select(RetailSyncJob).where(RetailSyncJob.shop==service.shop(),RetailSyncJob.status=='QUEUED').order_by(RetailSyncJob.id).limit(1).with_for_update(skip_locked=True)).scalar_one_or_none()
        if not job:db.commit();return False
        job.status='RUNNING';job.heartbeat=datetime.utcnow();rid=job.id;db.commit()
    run_sync(sessions,rid);return True
