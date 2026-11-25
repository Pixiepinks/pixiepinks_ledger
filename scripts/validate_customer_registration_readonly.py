"""Sanitized read-only pre/post-release comparison; no app startup or API calls."""
import argparse,hashlib,json
from sqlalchemy import text,inspect
from database import engine


def run(customer_id=None,historical_ids=None):
    out={'mode':'READ_ONLY','database_writes':0,'shopify_requests':0}
    try:
        with engine.connect() as c:
            tx=c.begin()
            try:
                if c.dialect.name!='postgresql':raise RuntimeError()
                c.execute(text('SET TRANSACTION READ ONLY'));out['transaction_read_only']=c.execute(text('SHOW transaction_read_only')).scalar()=='on'
                out['migration']=c.execute(text('SELECT version_num FROM alembic_version')).scalar()
                out['programme_enabled']=c.execute(text('SELECT enabled FROM loyalty_config WHERE id=1')).scalar()
                out['operational_row_counts']={t:c.execute(text('SELECT count(*) FROM '+t)).scalar() for t in ('journal_entries','sales_payments','pos_checkouts','inventory_events','hardware_jobs') if inspect(c).has_table(t)}
                out['customers']=c.execute(text('SELECT count(*) FROM customers')).scalar();out['members']=c.execute(text('SELECT count(*) FROM loyalty_members')).scalar()
                if customer_id is not None:out['selected_membership']=[dict(r) for r in c.execute(text('SELECT id,customer_id,status,qualifying_count,lifetime_net,lifetime_savings,source FROM loyalty_members WHERE customer_id=:cid'),{'cid':customer_id}).mappings()]
                out['pending_recalculations']=[dict(r) for r in c.execute(text('SELECT status,count(*) AS count FROM loyalty_recalculations GROUP BY status')).mappings()]
                columns={v['name'] for v in inspect(c).get_columns('customers')};out['registration_schema_present']='registration_source' in columns
                if out['registration_schema_present']:out['registered_by_source']=[dict(r) for r in c.execute(text('SELECT registration_source,count(*) AS count FROM customers GROUP BY registration_source')).mappings()]
                query='SELECT id,customer_id,order_status,payment_status,fulfillment_status,grand_total,amount_paid,shopify_order_id,shopify_draft_order_id,updated_at FROM sales_orders'
                parameters={}
                if historical_ids:
                    keys=[]
                    for i,oid in enumerate(historical_ids):keys.append(':i'+str(i));parameters['i'+str(i)]=oid
                    query+=' WHERE id IN ('+','.join(keys)+')'
                rows=[dict(r) for r in c.execute(text(query+' ORDER BY id'),parameters).mappings()]
                out['historical_rows_checked']=len(rows);out['historical_expected_rows']=len(set(historical_ids)) if historical_ids else None
                out['historical_fields_sha256']=hashlib.sha256(json.dumps(rows,default=str,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                out['status']='READ_ONLY_CHECK_COMPLETE'
            finally:tx.rollback()
    except Exception:out['status']='READ_ONLY_CHECK_UNAVAILABLE'
    return out
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--customer-id',type=int);p.add_argument('--historical-ids',type=int,nargs='*');args=p.parse_args()
    if args.customer_id is not None and not 0<args.customer_id<=2147483647:p.error('Invalid Customer ID')
    if args.historical_ids and (len(args.historical_ids)>1000 or any(i<=0 for i in args.historical_ids)):p.error('Use at most 1000 positive historical IDs')
    print(json.dumps(run(args.customer_id,args.historical_ids),default=str,indent=2))
