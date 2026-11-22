"""Authenticated read-only POS incident inspection; NEVER reconciles or dispatches.
Run inside the existing authorized runtime. No credentials belong in arguments.
"""
import argparse,json,re
from contextlib import contextmanager
from decimal import Decimal
from sqlalchemy import text
from database import SessionLocal
from models import Account,JournalEntry,JournalLine,SalesOrder,SalesPayment,User
from pos_models import POSCheckout,POSOperation,POSAudit,POSPosting,POSConfig,POSSession
from settings import settings
import pos_service as service
import pos_shopify as adapter
from pos_failure_codes import failure_code,sanitized

REFERENCES=['POS-851C4716B015DF1B0E79','POS-5AB126C311C38CDBD5FA']


class ReadOnlyClient(adapter.POSShopify):
    def request(self,query,variables):
        if query not in {adapter.READ,adapter.ORDER_CUSTOMER,adapter.FIND}:raise adapter.POSRemoteError('Diagnostic permits order reads/search only')
        return super().request(query,variables)
    def create(self,*args,**kwargs):raise adapter.POSRemoteError('Diagnostic mutations forbidden')
    def refund(self,*args,**kwargs):raise adapter.POSRemoteError('Diagnostic mutations forbidden')


def order_gid(value):return isinstance(value,str) and re.fullmatch(r'gid://shopify/Order/[1-9][0-9]*',value) is not None


@contextmanager
def read_only_session(sessions):
    with sessions() as db:
        dialect=db.bind.dialect.name;previous=None
        try:
            if dialect=='postgresql':db.execute(text('SET TRANSACTION READ ONLY'))
            elif dialect=='sqlite':
                previous=db.execute(text('PRAGMA query_only')).scalar()
                db.execute(text('PRAGMA query_only=ON'))
            else:raise ValueError('Unsupported read-only database')
            yield db
        finally:
            db.rollback()
            if previous is not None:
                db.execute(text('PRAGMA query_only='+str(int(bool(previous)))))
                db.rollback()


def run(sessions,client,references):
    if not references or len(references)>10 or any(not re.fullmatch(r'POS-[A-F0-9]{20}',ref) for ref in references):raise ValueError('Use at most 10 exact POS references')
    result={'mode':'READ_ONLY','write_authorized':False,'flags':{name:getattr(settings,name) for name in ['POS_ENABLED','POS_CHECKOUT_WRITE_ENABLED','POS_REFUND_WRITE_ENABLED','POS_CASH_DRAWER_AUTOMATION_ENABLED','PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED']},'sales':[]}
    with read_only_session(sessions) as db:
        shop=service.retail.shop()
        blocking=db.query(POSOperation).filter(POSOperation.shop==shop,POSOperation.status.in_(['DISPATCHING','UNCERTAIN','REVIEW'])).all()
        blockers={op.checkout_id for op in blocking}
        result['shop_blocking_operations']=[{'sale_reference':r.reference if r.reference in references else 'OTHER_SALE','kind':op.kind,'status':op.status,'attempts':op.attempts} for op in blocking if (r:=db.get(POSCheckout,op.checkout_id))]
        oldest=db.query(POSOperation).filter_by(shop=shop,status='QUEUED').order_by(POSOperation.updated_at).first()
        result['oldest_queued']={'kind':oldest.kind,'attempts':oldest.attempts,'write_gate_open':settings.POS_CHECKOUT_WRITE_ENABLED if oldest.kind=='CREATE' else settings.POS_REFUND_WRITE_ENABLED} if oldest else None
        for ref in references:
            r=db.query(POSCheckout).filter_by(shop=shop,reference=ref).one_or_none()
            if not r:result['sales'].append({'reference':ref,'status':'NOT_FOUND_IN_CONFIGURED_SHOP'});continue
            ops=db.query(POSOperation).filter_by(checkout_id=r.id).all();create=next((op for op in ops if op.kind=='CREATE'),None)
            summary={'reference':ref,'status':r.status,'blocks_shop_queue':r.id in blockers,'operation':{'status':create.status,'attempts':create.attempts,'error':create.error if create.error in {'READBACK_OR_POSTING_UNAVAILABLE','WORKER_INTERRUPTED','REMOTE_OUTCOME_UNKNOWN','POS_PREFLIGHT_CHANGED'} else None} if create else None}
            audit=db.query(POSAudit).filter_by(shop=shop,reference=create.id if create else '',action='READBACK_REVIEW').order_by(POSAudit.id.desc()).first()
            summary['recorded_diagnostic']=sanitized(audit.detail) if audit else None
            q=r.frozen['quote'];accounts=r.frozen['policy']['accounts']
            entries=[(accounts['sales'],Decimal(0),Decimal(q['subtotal'])),(accounts['discount'],Decimal(q['discount_total']),Decimal(0)),(accounts['tax'],Decimal(0),Decimal(q['tax_total']))]
            entries.extend((accounts[{'CASH':'cash','CARD_PAYMENT':'card','BANK_TRANSFER':'bank'}[t['method']]],Decimal(t['amount']),Decimal(0)) for t in r.frozen['tenders'])
            payments=db.query(SalesPayment).filter_by(sales_order_id=r.sales_order_id).all();posting=db.query(POSPosting).filter_by(reference='POS-sale:'+r.id).one_or_none()
            expected=sorted((a,str(d),str(c)) for a,d,c in entries if d or c)
            actual=sorted((line.account_id,str(Decimal(line.debit)),str(Decimal(line.credit))) for line in db.query(JournalLine).filter_by(entry_id=posting.entry_id)) if posting else None
            # Decimal equality, independent of textual scale (0 vs 0.00).
            normalize=lambda rows:sorted((a,Decimal(d),Decimal(c)) for a,d,c in rows) if rows is not None else None
            session=db.get(POSSession,r.session_id);config=db.get(POSConfig,shop)
            summary['local']={'grand_total':q['grand_total'],'frozen_hash_valid':service.digest(r.frozen)==r.frozen_hash,'accounts_exist':all(db.get(Account,a) is not None for a,_,_ in entries),'posting_balanced':sum((d-c for _,d,c in entries),Decimal(0))==0,'existing_posting':posting is not None,'journal_memo_count':db.query(JournalEntry).filter_by(memo='POS-sale:'+r.id).count(),'payment_accounting_link_count':sum(p.accounting_entry_id is not None for p in payments),'payment_links_match_posting':all(p.accounting_entry_id==posting.entry_id for p in payments) if posting else None,'existing_posting_matches':normalize(actual)==normalize(expected) if posting else None,'payment_count':len(payments),'verified_amount':str(sum((Decimal(p.amount) for p in payments if p.verification_status=='VERIFIED'),Decimal(0))),'all_tenders_verified':bool(payments) and all(p.verification_status=='VERIFIED' for p in payments),'session_status':session.status if session else None,'policy_version_matches':bool(config and config.version==r.frozen['config_version'])}
            known=(create.outcome or {}).get('remote_id') if create else None
            summary['remote']={'recorded_order_id':known if order_gid(known) else None,'customer_association_required':r.frozen.get('customer_shopify_id') is not None,'financial_read_includes_customer':False}
            matches=None
            try:
                matches=client.find(ref);summary['remote']['search_match_count']=len(matches)
                summary['remote']['search_order_ids']=[v.get('id') for v in matches if order_gid(v.get('id'))][:10]
            except Exception:summary['remote']['search_status']='UNAVAILABLE'
            target=known if order_gid(known) else matches[0].get('id') if matches is not None and len(matches)==1 else None
            if order_gid(target):
                stage='SHOPIFY_READBACK'
                try:
                    observed=service.read_order_for_verification(client,r,target);stage='ORDER_VERIFICATION'
                    if not isinstance(observed,dict) or observed.get('id')!=target:raise adapter.POSRemoteError('Order identity/correlation mismatch')
                    service.verify_order(r,observed)
                    summary['remote'].update(readback_status='VERIFIED_AGAINST_FROZEN_SALE',verified_order_id=target)
                except Exception as exc:summary['remote'].update(readback_status='NOT_VERIFIED',stage=stage,failure_code=failure_code(stage,exc))
            else:summary['remote']['readback_status']='NO_UNAMBIGUOUS_ORDER_TARGET'
            summary['existing_reconciliation_search_condition_met']=bool(matches is not None and len(matches)==1 and summary['remote']['readback_status']=='VERIFIED_AGAINST_FROZEN_SALE' and matches[0].get('id')==target)
            summary['write_authorized']=False
            result['sales'].append(summary)
        db.rollback()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--reference',action='append');args=parser.parse_args()
    try:result=run(SessionLocal,ReadOnlyClient(),args.reference or REFERENCES)
    except Exception:
        print(json.dumps({'mode':'READ_ONLY','status':'UNAVAILABLE','write_authorized':False,'reason':'Inspect runtime configuration/schema privately; no exception payload exported'}));return 1
    print(json.dumps(result,indent=2,sort_keys=True));return 0

if __name__=='__main__':raise SystemExit(main())
