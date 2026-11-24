"""Operator-only read diagnostic. No worker/startup import and no customer fields."""
import json
from sqlalchemy import text
from database import engine
from settings import settings
from shopify_catalog_service import ShopifyCatalogClient,ShopifyCatalogError

PROBE='''query LoyaltyHistoryCoverage {
 shop { myshopifyDomain }
 currentAppInstallation { accessScopes { handle } }
 orders(first:1, sortKey:CREATED_AT, reverse:false) { nodes { createdAt } pageInfo { hasNextPage } }
}'''

def remote(client):
    data=client.graphql(PROBE,{})
    scopes={s['handle'] for s in data['currentAppInstallation']['accessScopes']}
    return {'domain_matches':data['shop']['myshopifyDomain']==(settings.SHOPIFY_SHOP+'.myshopify.com'),
        'read_orders':'read_orders' in scopes,'read_all_orders':'read_all_orders' in scopes,
        'oldest_accessible_order_created_at':next((r.get('createdAt') for r in data['orders']['nodes']),None),
        'complete_historical_coverage_verified':False}

def run():
    out={'mode':'READ_ONLY','configured_api_version':settings.SHOPIFY_API_VERSION,'mutations':0,'complete_historical_coverage_verified':False}
    try:
        with engine.connect() as c:
            transaction=c.begin()
            try:
                if c.dialect.name=='postgresql':c.execute(text('SET TRANSACTION READ ONLY'))
                elif c.dialect.name=='sqlite':c.exec_driver_sql('PRAGMA query_only=ON')
                else:raise RuntimeError('Unsupported database')
                row=c.execute(text('SELECT count(*) AS local_orders, min(created_at) AS oldest_local_order, max(created_at) AS newest_local_order, sum(CASE WHEN customer_id IS NULL THEN 1 ELSE 0 END) AS unlinked_customers FROM sales_orders')).mappings().one()
                out['local_crm']=dict(row)
            finally:
                transaction.rollback()
                if c.dialect.name=='sqlite':c.exec_driver_sql('PRAGMA query_only=OFF')
        out['shopify']=remote(ShopifyCatalogClient());out['status']='READ_ONLY_COVERAGE_CHECK_COMPLETE'
    except (ShopifyCatalogError,KeyError,TypeError,RuntimeError):out['status']='READ_ONLY_COVERAGE_UNVERIFIED'
    except Exception:out['status']='READ_ONLY_LOCAL_SOURCE_UNAVAILABLE'
    return out
if __name__=='__main__':print(json.dumps(run(),default=str,indent=2))
