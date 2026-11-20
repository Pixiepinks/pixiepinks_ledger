"""Provisional documents: synthetic funds/Shopify only, no physical printing."""
import copy,io
from decimal import Decimal
import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,checkout,cash
from test_pos_receipts_phase4g import assert_geometry,text
from models import User,SalesOrder,SalesPayment,JournalEntry
from pos_models import POSCheckout,POSOperation,POSAudit,POSCashMovement,POSRefund
from hardware_models import HardwareJob
import pos_service as service
import pos_provisional_receipts as provisional
import pos_receipts as final


def issue(r,rid,confirmation=True):
    with r.sessions() as db:return provisional.generate(db,db.get(User,r.uid),rid,confirmation)


def snapshot(r,rid):
    with r.sessions() as db:
        sale=db.get(POSCheckout,rid);order=db.get(SalesOrder,sale.sales_order_id)
        return {'state':sale.status,'frozen':copy.deepcopy(sale.frozen),'hash':sale.frozen_hash,
            'order':(order.id,order.order_status,order.payment_status,order.amount_paid,order.shopify_order_id),
            'payments':[(p.id,p.amount,p.verification_status,p.accounting_entry_id) for p in db.query(SalesPayment).order_by(SalesPayment.id)],
            'operations':[(p.id,p.status,p.attempts,p.outcome,p.wire) for p in db.query(POSOperation).order_by(POSOperation.id)],
            'cash':[(m.id,m.amount) for m in db.query(POSCashMovement).order_by(POSCashMovement.id)],
            'counts':tuple(db.query(model).count() for model in [POSCheckout,SalesOrder,SalesPayment,JournalEntry,POSRefund,HardwareJob])}


@pytest.mark.parametrize('status',['QUEUED','REVIEW','UNCERTAIN'])
def test_verified_cash_80mm_pending_sale_audit_only(pos,status):
    rid=checkout(pos)
    with pos.sessions() as db:db.get(POSCheckout,rid).status=status;db.commit()
    before=snapshot(pos,rid);raw=issue(pos,rid);out=text(raw)
    assert_geometry(raw,80)
    for value in ['INVOICE','PROVISIONAL — FINAL CONFIRMATION PENDING','8,500.00','9,000.00','500.00','Remaining balance LKR']:
        assert value in " ".join(out.split())
    assert 'Payment: PAID / Shopify verified' not in out and 'SALES RECEIPT' not in out
    assert snapshot(pos,rid)==before and not pos.pos.calls and not pos.remote.mutations
    with pos.sessions() as db:
        audit=db.query(POSAudit).filter_by(action='PROVISIONAL_ISSUED').one()
        assert audit.detail['genuine_receipt_confirmed'] and audit.detail['revision']==1
        with pytest.raises(HTTPException):final.generate(db,db.get(User,pos.uid),rid)


def test_split_payment_prints_only_verified_funds_then_updates_revision(pos):
    rid=checkout(pos,[{'method':'CASH','amount':'3000','received':'3500','reference':'','confirmed':True},
        {'method':'CARD_PAYMENT','amount':'2000','received':'2000','reference':'terminal-test','confirmed':True},
        {'method':'BANK_TRANSFER','amount':'3500','received':'3500','reference':'bank-test','confirmed':False}])
    raw=issue(pos,rid);out=text(raw)
    assert '5,000.00' in out and '3,500.00' in out and 'Card payment' in out
    assert 'Bank transfer' not in out and 'NOT acknowledged as received' in ' '.join(out.split())
    with pos.sessions() as db:
        sale=db.get(POSCheckout,rid);payment=db.query(SalesPayment).filter_by(sales_order_id=sale.sales_order_id,payment_method='BANK_TRANSFER').one()
        service.verify_bank(db,db.get(User,pos.uid),rid,payment.id,'verified-bank-'+rid)
    revised=text(issue(pos,rid));assert 'Bank transfer' in revised and 'COPY / REPRINT' in revised
    with pos.sessions() as db:
        records=db.query(POSAudit).filter(POSAudit.action.in_(provisional.EVENTS)).order_by(POSAudit.id).all()
        assert [a.action for a in records]==['PROVISIONAL_ISSUED','PROVISIONAL_UPDATED']
        assert records[0].detail['document_id']==records[1].detail['document_id']
        assert records[0].detail['first_issued_at']==records[1].detail['first_issued_at']
        assert db.query(JournalEntry).count()==0
    assert not pos.pos.calls and not pos.remote.mutations


def test_reprint_preserves_identity_and_final_receipt_links_same_checkout(pos):
    rid=checkout(pos);first=text(issue(pos,rid));second=text(issue(pos,rid))
    assert 'COPY / REPRINT' not in first and 'COPY / REPRINT' in second
    with pos.sessions() as db:
        records=db.query(POSAudit).filter(POSAudit.action.in_(provisional.EVENTS)).order_by(POSAudit.id).all()
        assert len(records)==2 and records[0].detail['document_id']==records[1].detail['document_id']
        reference=db.get(POSCheckout,rid).reference
    service.dispatch_one(pos.sessions,pos.pos)
    with pos.sessions() as db:
        raw=final.generate(db,db.get(User,pos.uid),rid)
        assert 'Final receipt replaces provisional acknowledgement:' in ' '.join(text(raw).split())
        assert 'PPA-'+reference in text(raw) and 'SALES RECEIPT' in text(raw)
        assert db.query(POSCheckout).count()==1 and db.query(SalesPayment).count()==1
        assert db.query(JournalEntry).count()==1
    with pytest.raises(HTTPException):issue(pos,rid)
    assert len(pos.pos.calls)==1


@pytest.mark.parametrize('problem',['unverified','cancelled','test_a','test_b','refund','permission','confirmation','inconsistent','snapshot'])
def test_denials_create_no_document_or_financial_effect(pos,monkeypatch,problem):
    rid=checkout(pos)
    with pos.sessions() as db:
        sale=db.get(POSCheckout,rid);payment=db.query(SalesPayment).filter_by(sales_order_id=sale.sales_order_id).one()
        if problem=='unverified':payment.verification_status='PENDING'
        elif problem=='cancelled':sale.status='CANCELLED'
        elif problem=='test_a':monkeypatch.setattr(provisional,'TEST_REFERENCES',{sale.reference,'POS-5AB126C311C38CDBD5FA'})
        elif problem=='test_b':monkeypatch.setattr(provisional,'TEST_REFERENCES',{'POS-851C4716B015DF1B0E79',sale.reference})
        elif problem=='refund':db.add(POSOperation(id='refund-test',shop=sale.shop,checkout_id=rid,kind='REFUND',business_key='refund-test',status='UNCERTAIN',actor_id=pos.uid,plan={}))
        elif problem=='permission':db.get(User,pos.uid).role='STAFF';db.get(User,pos.uid).section_access=['pos.view']
        elif problem=='inconsistent':payment.verified_at=None
        elif problem=='snapshot':monkeypatch.setattr(service,'digest',lambda value:'invalid')
        db.commit()
    before=snapshot(pos,rid)
    with pytest.raises(HTTPException):issue(pos,rid,problem!='confirmation')
    assert snapshot(pos,rid)==before
    with pos.sessions() as db:assert not db.query(POSAudit).filter(POSAudit.action.in_(provisional.EVENTS)).count()
    assert not pos.pos.calls and not pos.remote.mutations


def test_endpoint_auth_csrf_confirmation_and_no_hardware(pos):
    rid=checkout(pos);url='/crm/pos/api/sales/'+rid+'/provisional/pdf'
    assert pos.client.post(url,json={'confirm_received':True}).status_code==403
    response=pos.client.post(url,headers={'X-CSRF-Token':pos.csrf},json={'confirm_received':False})
    assert response.status_code==422
    response=pos.client.post(url,headers={'X-CSRF-Token':pos.csrf},json={'confirm_received':True})
    assert response.status_code==200 and response.headers['content-type']=='application/pdf'
    assert response.headers['cache-control']=='private, no-store'
    assert 'provisional' in response.headers['content-disposition']
    with pos.sessions() as db:assert db.query(HardwareJob).count()==0 and db.query(JournalEntry).count()==0


def test_render_failure_does_not_consume_identity(pos,monkeypatch):
    rid=checkout(pos)
    def fail(*args,**kwargs):raise RuntimeError('synthetic renderer failure')
    monkeypatch.setattr(provisional,'render',fail)
    with pytest.raises(RuntimeError):issue(pos,rid)
    with pos.sessions() as db:assert db.query(POSAudit).filter(POSAudit.action.in_(provisional.EVENTS)).count()==0


def test_queue_fence_stays_blocked_after_provisional_print(pos):
    first=checkout(pos);pos.pos.fail_read=True;service.dispatch_one(pos.sessions,pos.pos)
    second=checkout(pos);before=snapshot(pos,second)
    issue(pos,second)
    assert service.dispatch_one(pos.sessions,pos.pos) is False
    assert snapshot(pos,second)==before and len(pos.pos.calls)==1


def test_exact_known_test_references_are_hard_denied():
    assert provisional.TEST_REFERENCES=={'POS-851C4716B015DF1B0E79','POS-5AB126C311C38CDBD5FA'}


@pytest.mark.parametrize('reference',sorted(provisional.TEST_REFERENCES))
def test_actual_designated_references_denied_by_endpoint(pos,monkeypatch,reference):
    monkeypatch.setattr(service.secrets,'token_hex',lambda count:reference.removeprefix('POS-').lower())
    rid=checkout(pos)
    response=pos.client.post('/crm/pos/api/sales/'+rid+'/provisional/pdf',headers={'X-CSRF-Token':pos.csrf},json={'confirm_received':True})
    assert response.status_code==409 and 'test transactions' in response.json()['detail']
    with pos.sessions() as db:assert db.query(POSAudit).filter(POSAudit.action.in_(provisional.EVENTS)).count()==0


def test_observed_shopify_refund_and_unauthenticated_access_denied(pos):
    rid=checkout(pos)
    with pos.sessions() as db:
        sale=db.get(POSCheckout,rid);db.get(SalesOrder,sale.sales_order_id).shopify_total_refunded=Decimal('1');db.commit()
    with pytest.raises(HTTPException):issue(pos,rid)
    pos.client.cookies.clear()
    response=pos.client.post('/crm/pos/api/sales/'+rid+'/provisional/pdf',headers={'X-CSRF-Token':pos.csrf},json={'confirm_received':True},follow_redirects=False)
    assert response.status_code in {303,401,403}
    with pos.sessions() as db:assert db.query(POSAudit).filter(POSAudit.action.in_(provisional.EVENTS)).count()==0


def test_long_unicode_tax_and_discounts_fit_measured_80mm_without_changing_snapshot():
    from datetime import datetime
    from test_pos_receipts_phase4g import sample
    import pos_money as calc
    sale=sample(12,title='ළමා පොත් Long water book with reusable learning activities '*3,variant='Pink / large / gift pack')
    sale.frozen['quote']=calc.calculate(sale.frozen['quote']['lines'],{'type':'FIXED','value':'100.00'},'EXCLUSIVE',Decimal('18'))
    sale.frozen['policy']['tax_title']='VAT 18%'
    total=Decimal(sale.frozen['quote']['grand_total'])
    meta={'document_id':'PPA-'+sale.reference,'revision':1,'duplicate':False,'issued_at':datetime(2026,11,20,12),'first_issued_at':datetime(2026,11,20,12)}
    paid=[{'id':1,'method':'CARD_PAYMENT','amount':str(total),'tender':{}}]
    before=copy.deepcopy(sale.frozen);raw=provisional.render(sale,meta,paid,total,Decimal(0))
    reader=assert_geometry(raw,80);assert len(reader.pages)==1
    assert 'VAT 18%' in text(raw) and '100.00' in text(raw) and 'Sri Lanka' in text(raw)
    assert sale.frozen==before
