"""Read-only transport/field isolation and exact POS reference lookup.
No reconciliation, CREATE/refund, inventory operation or production record edit.
"""
import argparse,hashlib,json,math,re
from dataclasses import dataclass,field
from uuid import UUID
import httpx
from sqlalchemy import func,text
from database import SessionLocal
from settings import settings
from shopify_catalog_service import ShopifyCatalogClient
from pos_models import POSCheckout,POSOperation
from models import SalesOrder
import pos_service as service
import pos_shopify as adapter
from pos_failure_codes import failure_code
from scripts.inspect_pos_sales_readonly import read_only_session,order_gid

SALE_A='POS-851C4716B015DF1B0E79'
SALE_B='POS-5AB126C311C38CDBD5FA'
ORDER_ID='gid://shopify/Order/7610874233135'
FIELDS={
 'identity':'id sourceIdentifier tags cancelledAt',
 'totals':'''id displayFinancialStatus totalPriceSet { shopMoney { amount currencyCode } }
 totalTaxSet { shopMoney { amount currencyCode } } totalDiscountsSet { shopMoney { amount currencyCode } }
 totalReceivedSet { shopMoney { amount currencyCode } } totalRefundedSet { shopMoney { amount currencyCode } }''',
 'customer':'id customer { id }',
 'lines':'''id lineItems(first:60) { nodes { id quantity refundableQuantity variant { id }
 originalUnitPriceSet { shopMoney { amount currencyCode } } customAttributes { key value } } pageInfo { hasNextPage endCursor } }''',
 'transactions':'id transactions(first:20) { id kind status gateway amountSet { shopMoney { amount currencyCode } } }',
 'fulfillment':'''id fulfillments(first:2) { id status location { id } fulfillmentLineItems(first:60) {
 nodes { quantity lineItem { id } } pageInfo { hasNextPage endCursor } } }''',
 'refunds':'''id refunds { id note totalRefundedSet { shopMoney { amount currencyCode } }
 refundLineItems(first:60) {nodes {quantity restockType location {id} lineItem {id}} pageInfo {hasNextPage endCursor}}
 transactions(first:2) {nodes {id kind status gateway parentTransaction {id} amountSet {shopMoney {amount currencyCode}}} pageInfo {hasNextPage endCursor}} }''',
}
QUERIES={name:'query POSReadIsolation($id:ID!) { shop { myshopifyDomain currencyCode } order(id:$id) { '+fields+' } }' for name,fields in FIELDS.items()}
QUERIES['full']=adapter.READ
TOKENS=set(re.findall(r'[A-Za-z_][A-Za-z0-9_]*',' '.join(QUERIES.values())))|{'Order','Customer','LineItem','Fulfillment','Refund','OrderTransaction','QueryRoot'}
ERROR_CODES={'ACCESS_DENIED','THROTTLED','MAX_COST_EXCEEDED','INTERNAL_SERVER_ERROR','SHOP_INACTIVE','BAD_REQUEST','GRAPHQL_VALIDATION_FAILED','undefinedField','argumentNotAccepted','variableRequiresValidType','variableMismatch','missingRequiredArguments','selectionMismatch','INVALID_QUERY','INVALID_VARIABLES','RESOURCE_NOT_FOUND','FORBIDDEN','UNAUTHORIZED'}


def number(value):return value if type(value) in {int,float} and 0<=value<=10**9 and math.isfinite(value) else None


def safe_errors(errors):
    result=[]
    for error in errors[:10] if isinstance(errors,list) else []:
        if not isinstance(error,dict):continue
        ext=error.get('extensions') or {};ext=ext if isinstance(ext,dict) else {}
        code=ext.get('code');path=error.get('path') or []
        row={'code':code if isinstance(code,str) and code in ERROR_CODES else 'UNCLASSIFIED'}
        if isinstance(path,list):row['path']=[part if isinstance(part,str) and part in TOKENS else part if type(part) is int and 0<=part<=10000 else 'REDACTED' for part in path[:16]]
        for key in ['fieldName','typeName']:
            if isinstance(ext.get(key),str) and ext[key] in TOKENS:row[key]=ext[key]
        message=error.get('message','');message=message.lower() if isinstance(message,str) else ''
        if 'access denied' in message or 'not approved to access' in message:row['category']='FIELD_ACCESS_OR_PROTECTED_DATA'
        elif "doesn't exist on type" in message or 'cannot query field' in message:row['category']='SCHEMA_FIELD_UNAVAILABLE'
        elif 'cost' in message and ('exceed' in message or 'maximum' in message):row['category']='QUERY_COST_LIMIT'
        else:row['category']='GRAPHQL_ERROR'
        result.append(row)
    return result


@dataclass
class ProbeResult:
    report:dict
    order:dict|None=field(default=None,repr=False)


class ReadOnlyTransport(ShopifyCatalogClient):
    def graphql(self,*args,**kwargs):raise ValueError('Use fixed diagnostic groups only')
    def create(self,*args,**kwargs):raise ValueError('Mutation forbidden')
    def refund(self,*args,**kwargs):raise ValueError('Mutation forbidden')
    def probe(self,group,order_id):
        if group not in QUERIES or not order_gid(order_id):raise ValueError('Invalid diagnostic target/group')
        report={'group':group,'requested_api_version':adapter.VERSION}
        try:token=self.get_token();base=self.base_url
        except Exception:return ProbeResult({**report,'status':'TOKEN_OR_CONFIGURATION_UNAVAILABLE'})
        try:
            response=self.http.post(base+'/admin/api/'+adapter.VERSION+'/graphql.json',headers={'X-Shopify-Access-Token':token},json={'query':QUERIES[group],'variables':{'id':order_id}},timeout=httpx.Timeout(12,connect=3))
        except httpx.TimeoutException:return ProbeResult({**report,'status':'TRANSPORT_TIMEOUT'})
        except Exception:return ProbeResult({**report,'status':'TRANSPORT_UNAVAILABLE'})
        report['http_status']=response.status_code
        version=response.headers.get('X-Shopify-API-Version','')
        report['response_api_version']=version if re.fullmatch(r'\d{4}-\d{2}',version) else 'MISSING_OR_INVALID'
        try:body=response.json()
        except ValueError:return ProbeResult({**report,'status':'NON_JSON_RESPONSE'})
        if not isinstance(body,dict):return ProbeResult({**report,'status':'INVALID_RESPONSE_SHAPE'})
        ext=body.get('extensions') or {};cost=ext.get('cost') if isinstance(ext,dict) else None
        if isinstance(cost,dict):
            throttle=cost.get('throttleStatus') or {};throttle=throttle if isinstance(throttle,dict) else {}
            report['cost']={key:number(cost.get(key)) for key in ['requestedQueryCost','actualQueryCost']}
            report['cost']['throttle']={key:number(throttle.get(key)) for key in ['currentlyAvailable','maximumAvailable','restoreRate']}
        if body.get('errors'):report['graphql_errors']=safe_errors(body['errors'])
        if response.status_code!=200:return ProbeResult({**report,'status':'HTTP_ERROR'})
        if version!=adapter.VERSION:return ProbeResult({**report,'status':'API_VERSION_MISMATCH'})
        if body.get('errors'):return ProbeResult({**report,'status':'GRAPHQL_ERROR'})
        data=body.get('data')
        if not isinstance(data,dict):return ProbeResult({**report,'status':'INVALID_DATA_SHAPE'})
        shop=data.get('shop') or {}
        if not isinstance(shop,dict) or shop.get('myshopifyDomain')!=base.removeprefix('https://') or shop.get('currencyCode')!='LKR':return ProbeResult({**report,'status':'SHOP_OR_CURRENCY_MISMATCH'})
        order=data.get('order')
        if order is None:return ProbeResult({**report,'status':'ORDER_NOT_RETURNED'})
        if not isinstance(order,dict) or order.get('id')!=order_id:return ProbeResult({**report,'status':'ORDER_ID_MISMATCH'})
        return ProbeResult({**report,'status':'READ_SUCCEEDED','verification_authorized':False},order)


def safe_shop(value):return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,254}',value) else 'INVALID_OR_NON_SLUG_FORMAT'


def row_metadata(db,row,configured):
    operations=db.query(POSOperation).filter_by(checkout_id=row.id).all()
    return {'checkout_id':str(UUID(row.id)),'reference':row.reference if re.fullmatch(r'\s*POS-[a-fA-F0-9]{20}\s*',row.reference,re.IGNORECASE) else 'NONSTANDARD_REFERENCE','stored_shop':safe_shop(row.shop),'configured_shop_match':row.shop==configured,'normalized_shop_match':row.shop.strip().lower()==configured,'status':row.status,'operations':[{'kind':op.kind,'status':op.status,'attempts':op.attempts} for op in operations]}


def sales_order_lookup(db,reference,configured):
    # Inspect the commercial record independently; absence of a checkout is not
    # evidence that the commercial order/payment is absent or may be recreated.
    rows=db.query(SalesOrder).filter_by(internal_order_number=reference).all()
    result=[]
    for order in rows:
        linked=db.query(POSCheckout).filter_by(sales_order_id=order.id).all()
        result.append({'sales_order_id':order.id,'sales_channel':order.sales_channel,
            'order_status':order.order_status,'payment_status':order.payment_status,
            'fulfillment_status':order.fulfillment_status,'shopify_sync_status':order.shopify_sync_status,
            'shopify_order_id':order.shopify_order_id if order_gid(order.shopify_order_id) else None,
            'linked_checkout_count':len(linked),
            'linked_checkouts':[row_metadata(db,row,configured) for row in linked]})
    return {'exact_match_count':len(rows),'records':result}


def reference_lookup(db,reference,configured):
    exact=db.query(POSCheckout).filter_by(reference=reference).all()
    current=[row for row in exact if row.shop==configured]
    if current:status='FOUND_IN_CONFIGURED_SHOP'
    elif exact:status='FOUND_IN_DIFFERENT_STORED_SHOP_CONTEXT'
    else:status='NOT_FOUND_IN_THIS_DATABASE_SCHEMA'
    result={'reference':reference,'status':status,'exact_matches':[row_metadata(db,row,configured) for row in exact],
        'crm_sales_orders':sales_order_lookup(db,reference,configured)}
    if not exact:
        normalized=db.query(POSCheckout).filter(func.upper(func.trim(POSCheckout.reference))==reference).limit(3).all()
        result['normalized_candidates']=[row_metadata(db,row,configured) for row in normalized]
        if normalized:result['status']='CASE_OR_WHITESPACE_REFERENCE_CANDIDATE'
    return result,current[0] if len(current)==1 else None


def run(sessions,client,order_id=ORDER_ID,groups=('full',),checkout_id=None):
    if not order_gid(order_id) or not groups or any(group not in QUERIES for group in groups) or len(groups)>8:raise ValueError('Invalid bounded diagnostic request')
    if checkout_id:checkout_id=str(UUID(checkout_id))
    result={'mode':'READ_ONLY','write_authorized':False,'target_order_id':order_id,'partial_groups_authorize_reconciliation':False}
    with read_only_session(sessions) as db:
        configured=service.retail.shop();result['configured_shop']=configured
        schema=None
        if db.bind.dialect.name=='postgresql':schema=db.execute(text("SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=to_regclass('pos_checkouts')")).scalar()
        url=db.bind.url
        identity='|'.join(str(value or '') for value in [url.drivername,url.host,url.port,url.database,schema])
        result['database_context_fingerprint']=hashlib.sha256(identity.encode()).hexdigest()
        result['resolved_pos_schema']=schema if isinstance(schema,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',schema) else None
        a,sale=reference_lookup(db,SALE_A,configured);b,_=reference_lookup(db,SALE_B,configured);result['sale_a_lookup']=a;result['sale_b_lookup']=b
        operations=db.query(POSOperation).filter_by(checkout_id=sale.id,kind='CREATE').all() if sale else []
        known=[(op.outcome or {}).get('remote_id') for op in operations if order_gid((op.outcome or {}).get('remote_id'))]
        result['target_matches_recorded_order']=len(known)==1 and known[0]==order_id
        if checkout_id:
            selected=db.get(POSCheckout,checkout_id);result['browser_checkout_lookup']=row_metadata(db,selected,configured) if selected else {'status':'UUID_NOT_FOUND_IN_THIS_DATABASE_SCHEMA'}
        required_customer=sale is not None and sale.frozen.get('customer_shopify_id') is not None
        result['customer_association_required']=required_customer if sale is not None else None
        result['financial_query_includes_customer']=False
        result['customer_fields_requested_separately']=['id']
        result['reads']=[];probes={}
        def probe_group(group):
            if group not in probes:probes[group]=client.probe(group,order_id)
            return probes[group]
        for group in groups:
            probe=probe_group(group);report=probe.report.copy()
            report['verification_role']='REQUIRED_ASSOCIATION' if group=='customer' and required_customer else 'OPTIONAL_PROTECTED_CUSTOMER' if group=='customer' else 'REQUIRED_FINANCIAL_FIELDS' if group=='full' else 'FIELD_ISOLATION_ONLY'
            if group=='full' and probe.order is not None:
                if sale is None:report['canonical_verification']='NO_UNAMBIGUOUS_LOCAL_SALE_A'
                elif not result['target_matches_recorded_order']:report['canonical_verification']='RECORDED_ORDER_MISMATCH'
                else:
                    try:
                        order=probe.order
                        if order.get('id')!=order_id:raise adapter.POSRemoteError('Order identity/correlation mismatch')
                        if required_customer and 'customer' not in order:
                            association=probe_group('customer');report['required_customer_read']=association.report.copy()
                            if association.order is None or 'customer' not in association.order:raise adapter.POSRemoteError('Required customer association unavailable')
                            if association.order.get('id')!=order_id:raise adapter.POSRemoteError('Order identity/correlation mismatch')
                            order={**order,'customer':association.order['customer']}
                        service.verify_order(sale,order);report['canonical_verification']='VERIFIED_AGAINST_FROZEN_SALE_A'
                    except Exception as exc:report.update(canonical_verification='NOT_VERIFIED',failure_code=failure_code('ORDER_VERIFICATION',exc))
            result['reads'].append(report)
            checks=[report,report.get('required_customer_read') or {}]
            if any(r.get('http_status')==429 or any(e.get('code')=='THROTTLED' for e in r.get('graphql_errors',[])) for r in checks):
                result['stopped_after_throttle']=True;break
        db.rollback()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--order-id',default=ORDER_ID);parser.add_argument('--groups',default='full');parser.add_argument('--checkout-id');args=parser.parse_args()
    try:result=run(SessionLocal,ReadOnlyTransport(),args.order_id,tuple(args.groups.split(',')),args.checkout_id)
    except Exception:
        print(json.dumps({'mode':'READ_ONLY','status':'UNAVAILABLE','write_authorized':False,'reason':'Check arguments/runtime/schema privately; exception text not exported'}));return 1
    print(json.dumps(result,indent=2,sort_keys=True));return 0

if __name__=='__main__':raise SystemExit(main())
