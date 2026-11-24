"""Sanitized, read-only local/Shopify diagnostic; no application startup imports."""
import argparse,json
from collections import Counter
from sqlalchemy import text
from sqlalchemy.orm import Session
from database import engine
from models import Customer,SalesOrder
import loyalty_recovery as recovery
from loyalty_shopify_history import READ,PAGE_SIZE,optional_identities
from scripts.validate_loyalty_history_readonly import remote
from shopify_catalog_service import ShopifyCatalogClient

def local(db):
    counts=Counter();channels=Counter();errors=Counter();rows=[];gids=set()
    for order in db.query(SalesOrder).order_by(SalesOrder.id):
        s=recovery.local_snapshot(db,order);p=recovery.proposal(db,s)
        counts['orders_reviewed']+=1;channels[s['channel']]+=1
        counts['linked_customer_ids']+=bool(s['customer_id']);counts['missing_customer_ids']+=not bool(s['customer_id'])
        counts['source_has_name']+=p['has_name'];counts['source_has_valid_mobile']+=bool(s['phone'])
        counts['positive_verified_purchase']+=bool(s['financial'] and s['financial']['net']>=recovery.svc.money(recovery.svc.rules_config(db)['minimum_purchase']))
        counts['recoverable_information']+=p['recoverable_information'];counts['unambiguous_existing_candidate']+=p['unambiguous'];counts['reviewed_creation_candidate']+=bool(p['can_create'])
        if s['financial_error']:errors[s['financial_error']]+=1
        if s['order_gid']:gids.add(s['order_gid'])
        rows.append({'order_id':order.id,'reference':order.internal_order_number,'channel':s['channel'],'customer_id_present':bool(s['customer_id']),'name_present':p['has_name'],'normalized_mobile_present':bool(s['phone']),'identity_conflict':s['identity_conflict'],'existing_candidate_count':len(p['candidates']),'unambiguous_candidate':p['unambiguous'],'reviewed_creation_possible':bool(p['can_create']),'financial_evidence':s['financial_error'] or 'VERIFIED_COMPLETED_PAID','positive_verified_net':bool(s['financial'] and s['financial']['net']>=recovery.svc.money(recovery.svc.rules_config(db)['minimum_purchase'])),'conversation_evidence_present':any(v['source']=='WHATSAPP_CONVERSATION' for v in s['sources']),'pos_snapshot_evidence_present':any(v['source']=='POS_FROZEN' for v in s['sources']),'payment_verified':s['payment_verified'],'completion_verified':s['completion_verified'],'shopify_order_link_present':bool(order.shopify_order_id)})
    return {'counts':dict(counts),'channels':dict(channels),'financial_exclusions':dict(errors),'customers_in_crm':db.query(Customer).count(),'orders':rows},gids

def shopify(client,local_gids,max_pages):
    result={**remote(client),'pagination_exhausted':False,'complete_shopify_history':False,'pages':0,'accessible_orders_examined':0,'local_order_matches':0,'protected_customer_access':'NOT_CHECKED','non_atomic_scan':True}
    if not result['domain_matches'] or not result['read_orders']:return {**result,'status':'IDENTITY_OR_SCOPE_UNVERIFIED'}
    cursor=None;seen=set();ids=set();oldest=None;latest=None
    try:
        for _ in range(max_pages):
            data=client.graphql(READ,{'after':cursor,'filter':'status:any'})['orders'];edges=data['edges'];info=data['pageInfo'];more=info['hasNextPage'];end=info['endCursor']
            if not isinstance(edges,list) or len(edges)>PAGE_SIZE or type(more) is not bool or edges and end!=edges[-1]['cursor'] or more and (not edges or not end or end==cursor or end in seen):raise ValueError()
            for edge in edges:
                node=edge['node'];gid=node['id']
                if gid in ids:raise ValueError()
                ids.add(gid);created=node['createdAt'];oldest=min(oldest,created) if oldest else created;latest=max(latest,created) if latest else created
            if result['protected_customer_access']=='NOT_CHECKED' and edges:_,result['protected_customer_access']=optional_identities(client,[edges[0]['node']['id']])
            result.update(pages=result['pages']+1,accessible_orders_examined=len(ids),local_order_matches=len(ids&local_gids),oldest_examined_created_at=oldest,newest_examined_created_at=latest)
            if not more:return {**result,'status':'ACCESSIBLE_PAGINATION_EXHAUSTED','pagination_exhausted':True,'accessible_orders_not_in_local_crm':len(ids-local_gids)}
            cursor=end;seen.add(end)
        return {**result,'status':'PAGE_BUDGET_REACHED','pagination_exhausted':False}
    except Exception:return {**result,'status':'READ_OR_PAGINATION_UNVERIFIED','pagination_exhausted':False}

def run(include_shopify=False,max_pages=10):
    out={'mode':'READ_ONLY','database_writes':0,'shopify_writes':0,'customer_details_redacted':True}
    try:
        with engine.connect() as connection:
            transaction=connection.begin()
            try:
                if connection.dialect.name=='postgresql':connection.execute(text('SET TRANSACTION READ ONLY'))
                elif connection.dialect.name=='sqlite':connection.exec_driver_sql('PRAGMA query_only=ON')
                else:raise RuntimeError()
                with Session(bind=connection,join_transaction_mode="create_savepoint") as db:out['local'],gids=local(db)
                out['transaction_read_only']=connection.execute(text('SHOW transaction_read_only')).scalar()=='on' if connection.dialect.name=='postgresql' else True
            finally:
                transaction.rollback()
                if connection.dialect.name=='sqlite':connection.exec_driver_sql('PRAGMA query_only=OFF')
        if include_shopify:out['shopify']=shopify(ShopifyCatalogClient(),gids,max_pages)
        out['status']='READ_ONLY_DIAGNOSTIC_COMPLETE'
    except Exception:out['status']='READ_ONLY_DIAGNOSTIC_UNAVAILABLE'
    return out
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--shopify',action='store_true');parser.add_argument('--max-pages',type=int,default=10);args=parser.parse_args()
    if not 1<=args.max_pages<=1000:parser.error('--max-pages must be 1–1000')
    print(json.dumps(run(args.shopify,args.max_pages),default=str,indent=2))
