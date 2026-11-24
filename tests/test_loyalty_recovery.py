"""Phase 2A: missing-link recovery never changes commerce or sends messages."""
import copy,json
from datetime import datetime,timedelta
from decimal import Decimal
from uuid import uuid4
import pytest
from fastapi import HTTPException
from models import Customer,SalesOrder,SalesPayment,User,JournalEntry
from loyalty_models import *
from test_loyalty import loyalty,inbox,source
import loyalty_service as svc
import loyalty_recovery as recovery
import loyalty_shopify_history as history
from shopify_catalog_service import ShopifyCatalogError


def plan(v,items):
    with v.sessions() as db:
        job=recovery.preview(db,db.get(User,v.uid),items,'recover-'+str(uuid4()));db.commit();return job.id,job.digest

def finish(v,jid,digest):
    with v.sessions() as db:
        job=db.get(LoyaltyRecoveryPlan,jid);u=db.get(User,v.uid)
        recovery.approve(db,u,job,digest,'Original identity evidence reviewed by manager');db.commit()
        recovery.execute(db,u,job,digest);db.commit();return job.results

def node(i=1,**changes):
    amount=lambda n:{'shopMoney':{'amount':n,'currencyCode':'LKR'}}
    return {'id':f'gid://shopify/Order/{i}','name':f'#{i}','createdAt':'2025-01-01T00:00:00Z','updatedAt':'2026-11-20T00:00:00Z','closedAt':'2026-11-20T00:00:00Z','cancelledAt':None,'test':False,'displayFinancialStatus':'PAID','displayFulfillmentStatus':'FULFILLED','currentTotalPriceSet':amount('4500.00'),'totalReceivedSet':amount('4500.00'),'totalRefundedSet':amount('0.00'),'totalOutstandingSet':amount('0.00'),**changes}

class Reader:
    def __init__(self,pages=None,denied=False,read_all=True):self.pages=pages or [([node()],False,'c1')];self.calls=[];self.denied=denied;self.read_all=read_all
    def graphql(self,q,v):
        assert 'mutation' not in q.lower();self.calls.append((q,v))
        if 'LoyaltyHistoryCoverage' in q:
            return {'shop':{'myshopifyDomain':'loyalty-test.myshopify.com'},'currentAppInstallation':{'accessScopes':[{'handle':'read_orders'}]+([{'handle':'read_all_orders'}] if self.read_all else [])},'orders':{'nodes':[{'createdAt':'2025-01-01T00:00:00Z'}]}}
        if 'LoyaltyOptionalCustomers' in q:
            if self.denied:raise ShopifyCatalogError('Protected customer read unavailable')
            return {'o'+str(i):{'id':gid,'customer':{'id':'gid://shopify/Customer/1','displayName':'Existing buyer','phone':'+94771234567'}} for i,gid in enumerate(v.values())}
        if q==history.READ:
            index=sum(call[0]==history.READ for call in self.calls)-1
            entries,more,cursor=self.pages[index]
            return {'orders':{'edges':[{'cursor':cursor if i==len(entries)-1 else f'{cursor}-{i}','node':n} for i,n in enumerate(entries)],'pageInfo':{'hasNextPage':more,'endCursor':cursor}}}
        raise AssertionError('Unexpected read query')

def retrieve(v,reader=None):
    reader=reader or Reader()
    with v.sessions() as db:
        u=db.get(User,v.uid);job=history.start(db,u,reader);db.commit();jid=job.id
        while job.status=='RUNNING':history.page(db,u,job,job.pages,reader);db.commit()
        rid=db.query(LoyaltyHistoricalOrder.id).first()
        return jid,rid[0] if rid else None


def test_production_sixteen_orders_missing_ids_have_recoverable_evidence(loyalty):
    v=loyalty
    ids=[source(v,customer=False) for _ in range(16)]
    with v.sessions() as db:
        job=svc.preview_migration(db,db.get(User,v.uid),{},'production-sixteen');db.commit()
        assert job.counts['orders_reviewed']==16 and job.counts['unique_customers']==0 and job.counts['missing_customer_details']==16
        d=recovery.dashboard(db);assert d['counts']['recoverable_information']==16 and d['counts']['linked_customers']==0
        rows=recovery.order_rows(db);assert len(rows)==16 and all(r['unambiguous'] for r in rows)
        assert all('phone' not in r and 'sources' not in r for r in rows)
        assert db.query(LoyaltyMember).count()==0
    jid,digest=plan(v,[{'kind':'LOCAL','id':oid,'customer_id':v.cid} for oid in ids]);finish(v,jid,digest)
    with v.sessions() as db:
        assert db.query(LoyaltyOrderIdentity).count()==16 and db.query(SalesOrder).filter(SalesOrder.customer_id.isnot(None)).count()==0
        assert db.query(LoyaltyMember).count()==0 and db.query(JournalEntry).count()==0
        assert svc.history(db,v.cid)['count']==16
        updated=svc.preview_migration(db,db.get(User,v.uid),{},'recovered-sixteen');db.commit()
        assert updated.counts['unique_customers']==1 and updated.counts['new_proposed']==1
    assert not v.sends


def test_reviewed_customer_creation_preserves_history_and_does_not_activate(loyalty):
    v=loyalty;oid=source(v,customer=False)
    with v.sessions() as db:
        o=db.get(SalesOrder,oid);o.customer_name='Another real buyer';o.whatsapp_phone='0779876543';db.commit()
        before=db.query(SalesPayment).count()
    jid,digest=plan(v,[{'kind':'LOCAL','id':oid,'action':'CREATE'}]);result=finish(v,jid,digest)
    with v.sessions() as db:
        c=db.get(Customer,result[0]['customer_id']);assert c.name=='Another real buyer' and c.phone=='+94779876543'
        assert db.query(LoyaltyMember).count()==0 and db.get(SalesOrder,oid).customer_id is None
        assert db.query(SalesPayment).count()==before and db.query(JournalEntry).count()==0 and not svc.config(db).enabled
        job=db.get(LoyaltyRecoveryPlan,jid);recovery.execute(db,db.get(User,v.uid),job,digest);db.commit()
        assert db.query(Customer).count()==2 and db.query(LoyaltyRecalculation).filter_by(customer_id=c.id).count()==0

@pytest.mark.parametrize('case',['missing_phone','shared_phone','different_name','conflicting_conversation','international'])
def test_identity_edge_cases_never_auto_merge(loyalty,case):
    from models import WhatsAppConversation
    v=loyalty;oid=source(v,customer=False)
    with v.sessions() as db:
        o=db.get(SalesOrder,oid)
        if case=='missing_phone':o.whatsapp_phone=''
        if case=='shared_phone':db.add(Customer(name='Different person',phone='+94771234567'))
        if case=='different_name':o.customer_name='Different person'
        if case=='international':o.whatsapp_phone='+442079460123';db.get(Customer,v.cid).phone='+442079460123'
        if case=='conflicting_conversation':
            c=db.query(WhatsAppConversation).first();o.crm_conversation_id=c.id
        db.commit();s=recovery.local_snapshot(db,o);p=recovery.proposal(db,s)
        assert p['unambiguous']==(case=='international')
        if case!='international':assert not p['can_create']
        assert svc.order_customer(db,o) is None

@pytest.mark.parametrize('failure',['unapproved','digest','expired','source_changed','target_changed','programme_on','shop_changed','candidate_changed'])
def test_recovery_approval_and_freshness_fail_closed(loyalty,failure):
    v=loyalty;oid=source(v,customer=False);jid,digest=plan(v,[{'kind':'LOCAL','id':oid,'customer_id':v.cid}])
    with v.sessions() as db:
        job=db.get(LoyaltyRecoveryPlan,jid);u=db.get(User,v.uid)
        if failure!='unapproved':recovery.approve(db,u,job,digest,'Verified source identity');db.commit()
        if failure=='expired':job.approved_at=datetime.utcnow()-timedelta(minutes=16)
        if failure=='source_changed':db.get(SalesOrder,oid).customer_name='Changed identity'
        if failure=='target_changed':db.get(Customer,v.cid).phone='0779876543'
        if failure=='candidate_changed':db.add(Customer(name='New shared mobile identity',phone='+94771234567'))
        if failure=='programme_on':svc.config(db).enabled=True
        if failure=='shop_changed':
            from unittest.mock import patch
            with patch.object(recovery.settings,'SHOPIFY_SHOP','wrong-shop'):
                with pytest.raises(HTTPException):recovery.execute(db,u,job,digest)
            return
        db.commit()
        with pytest.raises(HTTPException):recovery.execute(db,u,job,'0'*64 if failure=='digest' else digest)
        db.rollback();assert db.query(LoyaltyOrderIdentity).count()==0 and db.query(LoyaltyMember).count()==0


def test_uncertain_bulk_rejected_and_individual_review_retained(loyalty):
    v=loyalty;a=source(v,customer=False);b=source(v,customer=False)
    with v.sessions() as db:
        db.get(SalesOrder,b).customer_name='Mismatched name';db.commit()
        with pytest.raises(HTTPException):recovery.preview(db,db.get(User,v.uid),[{'kind':'LOCAL','id':i,'customer_id':v.cid} for i in (a,b)],'uncertain-bulk')
        db.rollback();assert db.query(LoyaltyRecoveryPlan).count()==0
    jid,digest=plan(v,[{'kind':'LOCAL','id':b,'customer_id':v.cid}]);finish(v,jid,digest)
    with v.sessions() as db:assert db.query(Customer).count()==1 and db.query(LoyaltyMember).count()==0


def test_complete_pagination_and_approved_remote_history_counts_once(loyalty):
    v=loyalty;reader=Reader([([node()],True,'c1'),([node(2)],False,'c2')]);jid,rid=retrieve(v,reader)
    with v.sessions() as db:
        job=db.get(LoyaltyHistoryScan,jid);assert job.pages==2 and job.orders_seen==2 and job.status=='PAGINATION_EXHAUSTED' and job.coverage['complete_shopify_history'] is False
        assert db.query(SalesOrder).count()==0 and db.query(LoyaltyMember).count()==0
    pid,digest=plan(v,[{'kind':'SHOPIFY','id':rid,'customer_id':v.cid}]);finish(v,pid,digest)
    with v.sessions() as db:
        assert svc.history(db,v.cid)['count']==1
        migration=svc.preview_migration(db,db.get(User,v.uid),{'source':'SHOPIFY_HISTORY'},'remote-migration-preview');db.commit()
        assert migration.counts['eligible_customers']==1 and migration.counts['orders_reviewed']==2
    oid=source(v)
    with v.sessions() as db:
        db.get(SalesOrder,oid).shopify_order_id='gid://shopify/Order/1';db.commit()
        assert svc.history(db,v.cid)['count']==1
        assert recovery.remote_financial(db,db.get(LoyaltyHistoricalOrder,rid))[1]=='DUPLICATE_LOCAL_CRM'

@pytest.mark.parametrize('scenario',['repeated_cursor','missing_cursor','duplicate_order','budget','access_denied','wrong_shop'])
def test_incomplete_scan_never_becomes_eligible(loyalty,scenario,monkeypatch):
    v=loyalty
    pages=[([node()],True,'c1'),([node(2)],False,'c2')]
    if scenario=='repeated_cursor':pages[1]=([node(2)],True,'c1')
    if scenario=='missing_cursor':pages[0]=([node()],True,None)
    if scenario=='duplicate_order':pages[1]=([node()],False,'c2')
    reader=Reader(pages)
    with v.sessions() as db:
        u=db.get(User,v.uid);job=history.start(db,u,reader);db.commit()
        if scenario=='budget':monkeypatch.setattr(history,'PAGE_LIMIT',0)
        if scenario=='access_denied':
            def denied(*args):raise ShopifyCatalogError('Unavailable')
            reader.graphql=denied
        if scenario=='wrong_shop':monkeypatch.setattr(history.settings,'SHOPIFY_SHOP','another-shop')
        if scenario=='wrong_shop':
            with pytest.raises(HTTPException):history.page(db,u,job,0,reader)
            db.rollback();return
        while job.status=='RUNNING':history.page(db,u,job,job.pages,reader);db.commit()
        assert job.status=='NEEDS_REVIEW' and not job.coverage['pagination_exhausted']
        for row in db.query(LoyaltyHistoricalOrder):assert recovery.remote_financial(db,row)[0] is None


def test_protected_customer_failure_does_not_poison_financial_evidence(loyalty):
    v=loyalty;jid,rid=retrieve(v,Reader(denied=True,read_all=False))
    with v.sessions() as db:
        job=db.get(LoyaltyHistoryScan,jid);row=db.get(LoyaltyHistoricalOrder,rid)
        assert job.status=='PAGINATION_EXHAUSTED' and job.coverage['scope_window_warning'] and 'UNAVAILABLE' in job.coverage['protected_customer_access']
        assert row.identity=={} and not recovery.proposal(db,recovery.remote_snapshot(db,row))['can_create']
        assert svc.history(db,v.cid)['count']==0 and db.query(Customer).count()==1

@pytest.mark.parametrize('change,error',[({'test':True},'TEST_ORDER'),({'cancelledAt':'2026-11-21T00:00:00Z'},'CANCELLED_OR_RETURNED'),({'displayFulfillmentStatus':'UNFULFILLED'},'NOT_COMPLETED'),({'displayFinancialStatus':'PENDING'},'PAYMENT_NOT_VERIFIED')])
def test_remote_ineligible_orders_never_qualify(loyalty,change,error):
    v=loyalty;_,rid=retrieve(v,Reader([([node(**change)],False,'c1')]))
    with v.sessions() as db:assert recovery.remote_financial(db,db.get(LoyaltyHistoricalOrder,rid))[1]==error


def test_refund_staleness_and_rescan_approval_invalidation(loyalty):
    v=loyalty;_,rid=retrieve(v)
    pid,digest=plan(v,[{'kind':'SHOPIFY','id':rid,'customer_id':v.cid}]);finish(v,pid,digest)
    with v.sessions() as db:
        row=db.get(LoyaltyHistoricalOrder,rid);row.fetched_at=datetime.utcnow()-timedelta(hours=25);db.commit()
        assert svc.history(db,v.cid)['count']==0
    refunded=node(totalRefundedSet={'shopMoney':{'amount':'4500.00','currencyCode':'LKR'}},displayFinancialStatus='REFUNDED')
    retrieve(v,Reader([([refunded],False,'newcursor')]))
    with v.sessions() as db:
        row=db.get(LoyaltyHistoricalOrder,rid);assert row.approved_fingerprint!=row.fingerprint and svc.history(db,v.cid)['count']==0


def test_readonly_diagnostic_is_sanitized_and_no_creation(loyalty):
    from scripts.validate_loyalty_recovery_readonly import local,shopify
    v=loyalty;source(v,customer=False)
    with v.sessions() as db:
        before=db.query(LoyaltyAudit).count();result,gids=local(db)
        serialized=json.dumps(result);assert 'Existing buyer' not in serialized and '0771234567' not in serialized and '+94771234567' not in serialized
        assert result['counts']['missing_customer_ids']==1 and db.query(LoyaltyAudit).count()==before
    r=shopify(Reader(),gids,10);assert r['pagination_exhausted'] and not r['complete_shopify_history']


def test_recovery_permissions_csrf_and_no_financial_mutations(loyalty):
    v=loyalty;oid=source(v,customer=False);items=[{'kind':'LOCAL','id':oid,'customer_id':v.cid}]
    assert v.client.post('/crm/loyalty/api/recovery/plans',json={'items':items}).status_code==403
    h={'X-CSRF-Token':v.csrf,'Idempotency-Key':'recovery-api-unique'}
    response=v.client.post('/crm/loyalty/api/recovery/plans',json={'items':items},headers=h);assert response.status_code==200,response.text
    p=response.json();assert p['status']=='PREVIEW'
    assert v.client.post(f"/crm/loyalty/api/recovery/plans/{p['id']}/execute",json={'digest':p['digest']},headers=h).status_code==409
    v.login('quick-staff')
    assert v.client.get('/crm/loyalty/api/recovery/orders').status_code==403
    with v.sessions() as db:
        db.query(User).filter_by(username='quick-staff').one().section_access=['loyalty.migration.preview'];db.commit()
    assert v.client.get('/crm/loyalty/api/recovery').status_code==200
    assert v.client.get('/crm/loyalty/api/recovery/orders/LOCAL/'+str(oid)).status_code==403
    with v.sessions() as db:assert db.query(JournalEntry).count()==0 and db.query(LoyaltyMember).count()==0


def test_remote_source_changes_after_membership_preview_are_reviewed(loyalty):
    v=loyalty;_,rid=retrieve(v);pid,digest=plan(v,[{'kind':'SHOPIFY','id':rid,'customer_id':v.cid}]);finish(v,pid,digest)
    with v.sessions() as db:
        u=db.get(User,v.uid);job=svc.preview_migration(db,u,{},'remote-changing-source');db.commit()
        svc.approve_migration(db,u,job.id,job.digest,'Reviewed fresh historical evidence');db.commit()
        row=db.get(LoyaltyHistoricalOrder,rid);row.fetched_at=datetime.utcnow()-timedelta(days=2);db.commit()
        svc.execute_migration_batch(db,u,job.id,job.digest);db.commit()
        assert db.query(LoyaltyMember).count()==0 and db.query(LoyaltyMigrationRow).filter_by(migration_id=job.id,status='REVIEW').count()==1


def test_protected_query_separate_and_failed_scan_reports_incomplete(loyalty):
    from scripts.validate_loyalty_recovery_readonly import shopify
    v=loyalty
    assert 'customer' not in history.READ.lower() and 'lineItems' not in history.READ and 'first:20' in history.READ
    reader=Reader([([node()],True,'c1')])
    result=shopify(reader,set(),1)
    assert result['status']=='PAGE_BUDGET_REACHED' and not result['pagination_exhausted']
    assert all('mutation' not in q.lower() for q,_ in reader.calls)

@pytest.mark.parametrize('refund,status,current,net,error',[
 ('4500','REFUNDED','0','0',None),('1000','PARTIALLY_REFUNDED','3500','3500',None),
 ('0','REFUNDED','4500',None,'REFUND_EVIDENCE_UNAVAILABLE'),('4500','PAID','4500',None,'REFUND_EVIDENCE_UNAVAILABLE')])
def test_remote_refund_evidence_distinguishes_paid_from_eligible(loyalty,refund,status,current,net,error):
    v=loyalty
    amount=lambda n:{'shopMoney':{'amount':n,'currencyCode':'LKR'}}
    _,rid=retrieve(v,Reader([([node(displayFinancialStatus=status,totalRefundedSet=amount(refund),currentTotalPriceSet=amount(current))],False,'c1')]))
    with v.sessions() as db:
        value,actual=recovery.remote_financial(db,db.get(LoyaltyHistoricalOrder,rid));assert actual==error
        if net is not None:assert value['net']==Decimal(net)
