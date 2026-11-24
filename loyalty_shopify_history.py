"""Explicitly requested read-only Shopify history retrieval; local evidence only."""
from datetime import datetime
from uuid import uuid4
from models import SalesOrder
from loyalty_models import LoyaltyHistoryScan,LoyaltyHistoricalOrder
from shopify_catalog_service import ShopifyCatalogClient,ShopifyCatalogError
from scripts.validate_loyalty_history_readonly import remote as probe
from settings import settings
import loyalty_service as svc
import loyalty_recovery as recovery

PAGE_SIZE=20
PAGE_LIMIT=1000
ORDER_FIELDS='''id name createdAt updatedAt closedAt cancelledAt test
 displayFinancialStatus displayFulfillmentStatus
 currentTotalPriceSet { shopMoney { amount currencyCode } }
 totalReceivedSet { shopMoney { amount currencyCode } }
 totalRefundedSet { shopMoney { amount currencyCode } }
 totalOutstandingSet { shopMoney { amount currencyCode } }'''
READ='''query LoyaltyHistoricalPage($after:String,$filter:String!) {
 orders(first:20,after:$after,sortKey:CREATED_AT,query:$filter) {
  edges { cursor node { '''+ORDER_FIELDS+''' } }
  pageInfo { hasNextPage endCursor }
 }
}'''

def start(db,user,client=None):
    recovery.require_off(db);client=client or ShopifyCatalogClient()
    try:coverage=probe(client)
    except (ShopifyCatalogError,KeyError,TypeError):svc.fail('Read-only Shopify scope/identity verification unavailable',503)
    if not coverage['domain_matches'] or not coverage['read_orders']:svc.fail('Configured shop identity and read_orders must be verified',409)
    job=LoyaltyHistoryScan(id=str(uuid4()),shop=(settings.SHOPIFY_SHOP or '').lower(),actor_id=user.id,status='RUNNING',cursor=None,seen_cursors=[],pages=0,orders_seen=0,coverage={**coverage,'complete_shopify_history':False,'pagination_exhausted':False,'protected_customer_access':'NOT_CHECKED','local_import_coverage':'NOT_VERIFIED','scope_window_warning':not coverage['read_all_orders'],'catalog_consistency':'NON_ATOMIC_READ; concurrent changes remain possible'})
    db.add(job);db.flush();svc.audit(db,'HISTORY_SCAN_STARTED',job.id,user.username,None,{'shop':job.shop,'shopify_writes':0});return job

def optional_identities(client,gids):
    if not gids:return {},'NO_ORDERS'
    # All financial evidence is read independently from protected customer data.
    declarations=','.join(f'$i{i}:ID!' for i in range(len(gids)))
    fields=' '.join(f'o{i}:order(id:$i{i}){{id customer{{id displayName phone}}}}' for i in range(len(gids)))
    try:
        data=client.graphql('query LoyaltyOptionalCustomers('+declarations+'){'+fields+'}',{'i'+str(i):gid for i,gid in enumerate(gids)})
        result={}
        for i,gid in enumerate(gids):
            order=data.get('o'+str(i));customer=order.get('customer') if order and order.get('id')==gid else None
            if customer:
                name=customer.get('displayName');phone=svc.phone(customer.get('phone'))
                result[gid]={'shopify_customer_id':customer.get('id'),'name':str(name)[:255] if name else None,'phone':phone}
        return result,'READ_SUCCEEDED'
    except (ShopifyCatalogError,KeyError,TypeError):return {},'UNAVAILABLE_OR_PROTECTED; no identity inferred'

def page(db,user,job,expected_pages,client=None):
    recovery.require_off(db)
    if job.shop!=(settings.SHOPIFY_SHOP or '').lower():svc.fail('Scan shop differs from configured shop',409)
    if job.status=='PAGINATION_EXHAUSTED':return job
    if job.status!='RUNNING' or type(expected_pages) is not int or expected_pages!=job.pages:svc.fail('Refresh scan progress before requesting the next page',409)
    if job.pages>=PAGE_LIMIT:
        job.status='NEEDS_REVIEW';job.coverage={**job.coverage,'blocker':'PAGE_BUDGET_EXCEEDED'};return job
    client=client or ShopifyCatalogClient()
    try:
        # Recheck actual shop and scopes on every resumed page, not just at creation.
        coverage=probe(client)
        if not coverage['domain_matches'] or not coverage['read_orders']:raise ValueError()
        data=client.graphql(READ,{'after':job.cursor,'filter':"status:any created_at:<='"+job.started_at.isoformat(timespec='seconds')+"Z'"})['orders']
        edges=data['edges'];info=data['pageInfo'];more=info['hasNextPage'];cursor=info['endCursor']
        if not isinstance(edges,list) or len(edges)>PAGE_SIZE or type(more) is not bool:raise ValueError()
        if more and (not edges or not isinstance(cursor,str) or cursor==job.cursor or cursor in job.seen_cursors):raise ValueError()
        if edges and (not isinstance(cursor,str) or cursor!=edges[-1]['cursor']):raise ValueError()
        gids=[]
        required={'id','name','createdAt','updatedAt','closedAt','cancelledAt','test','displayFinancialStatus','displayFulfillmentStatus','currentTotalPriceSet','totalReceivedSet','totalRefundedSet','totalOutstandingSet'}
        for edge in edges:
            node=edge['node'];gid=node['id']
            if not isinstance(gid,str) or not __import__('re').fullmatch(r'gid://shopify/Order/[0-9]+',gid) or gid in gids or not required<=node.keys() or type(node['test']) is not bool:raise ValueError()
            if svc.stamp(node['createdAt'])>job.started_at:raise ValueError()
            svc.stamp(node['updatedAt'])
            for key in ('currentTotalPriceSet','totalReceivedSet','totalRefundedSet','totalOutstandingSet'):
                svc.money(node[key]['shopMoney']['amount'])
                if not isinstance(node[key]['shopMoney']['currencyCode'],str):raise ValueError()
            if db.query(LoyaltyHistoricalOrder.id).filter_by(scan_id=job.id,order_gid=gid).first():raise ValueError()
            gids.append(gid)
    except Exception:
        # No customer or raw GraphQL error details are logged or returned.
        job.status='NEEDS_REVIEW';job.coverage={**job.coverage,'blocker':'QUERY_OR_PAGINATION_UNVERIFIED','pagination_exhausted':False};return job
    identities,access=optional_identities(client,gids)
    for edge in edges:
        node=edge['node'];row=db.query(LoyaltyHistoricalOrder).filter_by(shop=job.shop,order_gid=node['id']).with_for_update().one_or_none()
        if not row:row=LoyaltyHistoricalOrder(shop=job.shop,order_gid=node['id']);db.add(row)
        # A denied optional read invalidates prior approval rather than reusing old PII.
        row.evidence=node;row.identity=identities.get(node['id'],{});row.fingerprint=svc.digest({'shop':job.shop,'evidence':node,'identity':row.identity});row.scan_id=job.id;row.fetched_at=datetime.utcnow()
    job.pages+=1;job.orders_seen+=len(edges);job.cursor=cursor
    job.seen_cursors=[*job.seen_cursors,*([cursor] if cursor else [])]
    previous_access=job.coverage.get('protected_customer_access')
    mixed='PARTIAL_OR_UNAVAILABLE' if previous_access not in {None,'NOT_CHECKED',access} else access
    job.coverage={**job.coverage,'protected_customer_access':mixed,'pagination_exhausted':not more,'read_all_orders':coverage['read_all_orders'],'scope_window_warning':not coverage['read_all_orders']}
    if not more:job.status='PAGINATION_EXHAUSTED';job.completed_at=datetime.utcnow()
    svc.audit(db,'HISTORY_SCAN_PAGE',job.id,user.username,None,{'pages':job.pages,'orders_seen':job.orders_seen,'pagination_exhausted':not more,'shopify_writes':0});return job
