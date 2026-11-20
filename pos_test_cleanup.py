"""Narrow local retirement of the two owner-confirmed November test sales.
Never calls Shopify mutations. Never deletes records or reverses posted journals.
"""
from datetime import datetime
from decimal import Decimal
from fastapi import HTTPException
from models import SalesOrder, SalesPayment, JournalEntry
from pos_models import POSCheckout, POSOperation, POSPosting, POSCashMovement, POSSession, POSRefund, POSAudit
import pos_service as pos

SHOP='eb2b70'
AMOUNT=Decimal('350')
A='POS-851C4716B015DF1B0E79'
B='POS-5AB126C311C38CDBD5FA'
ORDER='gid://shopify/Order/7610874233135'
CONFIRM='CANCEL TEST DATA '+A+' '+B+'; NO SHOPIFY CLEANUP'


def stop(condition,message):
    if not condition:raise HTTPException(409,message)


def plan(db,user,client):
    pos.require(user,'admin',True)
    stop(pos.retail.shop()==SHOP,'Configured shop differs from approved test shop')
    stop(not db.query(POSOperation).filter_by(shop=SHOP,status='DISPATCHING').first(),'A Shopify operation is dispatching; wait for a verified idle worker boundary')
    report={'shop':SHOP,'mode':'READ_ONLY_DRY_RUN','shopify_mutations':0,'journal_changes':0,'deletions':0,'sales':[],'sessions':[]}
    session_deltas={}
    for ref in (A,B):
        rows=db.query(POSCheckout).filter_by(reference=ref).all()
        stop(len(rows)==1,'Exact test checkout missing or ambiguous: '+ref)
        r=rows[0];stop(r.shop==SHOP,'Test checkout belongs to a different shop')
        order=db.get(SalesOrder,r.sales_order_id)
        stop(order and order.sales_channel=='POS' and order.internal_order_number==ref,'Commercial order/checkout identity mismatch')
        stop(db.query(POSCheckout).filter_by(sales_order_id=order.id).count()==1,'Ambiguous commercial order link')
        stop(pos.digest(r.frozen)==r.frozen_hash,'Frozen snapshot changed')
        stop(Decimal(r.frozen['quote']['grand_total'])==AMOUNT and Decimal(order.grand_total)==AMOUNT,'Unexpected test total')
        ops=db.query(POSOperation).filter_by(checkout_id=r.id).all()
        stop(len(ops)==1 and ops[0].kind=='CREATE' and ops[0].shop==SHOP,'Unexpected operation history')
        op=ops[0]
        stop((r.status,op.status,op.attempts)==(('REVIEW','REVIEW',1) if ref==A else ('QUEUED','QUEUED',0)),'Test operation changed; no retry or uncertainty bypass')
        stop(not db.query(POSRefund).filter_by(checkout_id=r.id).first(),'Refund history requires separate accounting review')
        stop(not db.query(POSPosting).filter_by(reference='POS-sale:'+r.id).first() and not db.query(JournalEntry).filter_by(memo='POS-sale:'+r.id).first(),'Posted accounting requires a separately approved reversal')
        ps=db.query(SalesPayment).filter_by(sales_order_id=order.id).all()
        stop(len(ps)==1 and ps[0].source=='POS' and ps[0].payment_method=='CASH' and ps[0].verification_status=='VERIFIED' and Decimal(ps[0].amount)==AMOUNT and ps[0].accounting_entry_id is None,'Unexpected tender or accounting linkage')
        tenders=r.frozen.get('tenders',[])
        stop(len(tenders)==1 and tenders[0]['method']=='CASH' and Decimal(tenders[0]['amount'])==AMOUNT,'Unexpected frozen tender')
        original='poscash:'+r.id+':0';correction='postest:'+r.id
        movements=db.query(POSCashMovement).filter_by(session_id=r.session_id).all()
        attached=[m for m in movements if (m.detail or {}).get('sale')==ref or m.reference==original]
        stop(len(attached)==1 and attached[0].reference==original and attached[0].kind=='SALE_TENDER' and Decimal(attached[0].amount)==AMOUNT,'Original register tender is missing or inconsistent')
        stop(not db.query(POSCashMovement).filter_by(reference=correction).first(),'Test cash already corrected')
        stop(not db.query(POSAudit).filter_by(shop=SHOP,reference=ref,action='TEST_DATA_RETIRED').first(),'Test sale already retired')
        session=db.get(POSSession,r.session_id)
        stop(session and session.shop==SHOP and session.status=='OPEN','Closed or different-shop session requires separate register review')
        matches=client.find(ref)
        if ref==A:
            stop((op.outcome or {}).get('remote_id')==ORDER,'Recorded Shopify order identity changed')
            stop(len(matches)==1 and matches[0].get('id')==ORDER,'Shopify A search identity is not unique')
            remote=client.read(ORDER)
            stop(remote and remote.get('id')==ORDER,'Shopify A full read unavailable')
            pos.verify_order(r,remote)
            stop(not order.shopify_order_id or order.shopify_order_id==ORDER,'Existing commercial Shopify link differs')
            remote_state='EXISTING_ORDER_VERIFIED_LEFT_UNCHANGED'
        else:
            stop(not matches and not op.outcome and not order.shopify_order_id,'B has a possible Shopify outcome; separate cleanup review required')
            remote_state='NO_SOURCE_CORRELATED_ORDER_FOUND'
        report['sales'].append({'reference':ref,'checkout_id':r.id,'sales_order_id':order.id,'operation_id':op.id,'payment_id':ps[0].id,'cash_movement_id':attached[0].id,'session_id':session.id,'before':{'checkout':r.status,'operation':op.status,'attempts':op.attempts,'order':order.order_status,'payment':ps[0].verification_status,'amount_paid':str(order.amount_paid),'frozen_hash':r.frozen_hash,'operation_updated_at':op.updated_at.isoformat() if op.updated_at else None},'after':{'checkout':'CANCELLED','operation':'CANCELLED','order':'CANCELLED','payment':'REJECTED','amount_paid':'0.00'},'cash_correction':f'{-AMOUNT:.2f}','remote_evidence':remote_state,'remote_id':ORDER if ref==A else None})
        session_deltas[session.id]=session_deltas.get(session.id,Decimal(0))-AMOUNT
    for sid,delta in sorted(session_deltas.items()):
        session=db.get(POSSession,sid);before=pos.drawer(db,session)
        report['sessions'].append({'session_id':sid,'expected_cash_before':str(before),'correction':str(delta),'expected_cash_after':str(before+delta)})
    report['other_queued_jobs']=db.query(POSOperation).filter_by(shop=SHOP,status='QUEUED').filter(POSOperation.id.notin_([r['operation_id'] for r in report['sales']])).count()
    report['other_queue_blockers']=db.query(POSOperation).filter(POSOperation.shop==SHOP,POSOperation.status.in_(['REVIEW','UNCERTAIN','DISPATCHING']),POSOperation.id.notin_([r['operation_id'] for r in report['sales']])).count()
    report['cash_correction_total']=f'{-2*AMOUNT:.2f}'
    report['plan_sha256']=pos.digest(report)
    return report


def apply(db,user,client,approved_hash,confirmation,notes):
    """Caller owns transaction: fresh checks and both cancellations under worker lock."""
    pos.require(user,'admin',True)
    stop(confirmation==CONFIRM,'Exact two-sale confirmation required')
    stop(isinstance(notes,str) and 20<=len(notes.strip())<=500,'Administrator evidence notes required')
    pos.locked(db)
    fresh=plan(db,user,client)
    stop(fresh['plan_sha256']==approved_hash,'Dry-run plan changed; obtain fresh approval')
    for item in fresh['sales']:
        r=db.get(POSCheckout,item['checkout_id']);op=db.get(POSOperation,item['operation_id'])
        order=db.get(SalesOrder,item['sales_order_id']);payment=db.get(SalesPayment,item['payment_id'])
        # Preserve attempts, remote ID, wire, frozen data, errors and prior audits.
        op.status='CANCELLED';op.updated_at=datetime.utcnow();r.status='CANCELLED'
        payment.verification_status='REJECTED';payment.rejected_by=user.username;payment.rejected_at=datetime.utcnow()
        payment.rejection_reason='Owner-confirmed simulated test tender; no physical cash refund: '+notes
        pos.payments._audit(db,payment,'POS_TEST_TENDER_RETIRED','VERIFIED','REJECTED',user.username,'postest:'+str(payment.id),notes)
        order.order_status='CANCELLED';pos.payments._derive_summary(db,order)
        db.add(POSCashMovement(session_id=r.session_id,amount=-AMOUNT,kind='TEST_TENDER_CORRECTION',reference='postest:'+r.id,actor_id=user.id,detail={'sale':r.reference,'original_cash_movement_id':item['cash_movement_id'],'physical_cash_return':False,'reason':notes}))
        pos.audit(db,user,r.reference,'TEST_DATA_RETIRED',plan_sha256=approved_hash,notes=notes,shopify_left_unchanged=True,original_operation_status=item['before']['operation'])
    db.flush()
    return {'status':'LOCAL_TEST_DATA_RETIRED','plan_sha256':approved_hash,'shopify_mutations':0,'cash_correction_total':f'{-2*AMOUNT:.2f}'}
