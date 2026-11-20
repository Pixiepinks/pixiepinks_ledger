"""Separate non-final payment acknowledgement. Only document audit writes allowed."""
from datetime import datetime
from decimal import Decimal
from math import ceil
from pathlib import Path
from fastapi import HTTPException
from models import SalesOrder, SalesPayment
from pos_models import POSAudit, POSRefund, POSOperation
from pos_receipts import ReceiptLayout, lkr, local_time, METHODS
import pos_service as service

TEST_REFERENCES={'POS-851C4716B015DF1B0E79','POS-5AB126C311C38CDBD5FA'}
STATES={'AWAITING_PAYMENT','PAYMENT_READY','QUEUED','REVIEW','UNCERTAIN','CONFLICT'}
EVENTS={'PROVISIONAL_ISSUED','PROVISIONAL_REPRINTED','PROVISIONAL_UPDATED'}
LOGO=Path(__file__).parent/'static'/'images'/'login_logo.png'
CONTACTS=('Address: 213/5, Magammana, Homagama','info@pixiepinks.shop','0705755777','www.pixiepinks.shop')
PAYMENT_LABELS={'CASH':'Cash','CARD_PAYMENT':'Card Payment','BANK_TRANSFER':'Bank Transfer'}


def require(condition,message):
    if not condition:raise HTTPException(409,message)


def evidence(db,sale):
    require(sale.reference not in TEST_REFERENCES,'Designated test transactions cannot receive customer acknowledgements')
    require(sale.status in STATES,'Only pending POS sales may receive a provisional acknowledgement; use the final receipt for COMPLETE')
    require(service.digest(sale.frozen)==sale.frozen_hash,'Frozen sale identity is invalid')
    order=db.get(SalesOrder,sale.sales_order_id)
    require(order and order.sales_channel=='POS' and order.internal_order_number==sale.reference and order.internal_order_number not in TEST_REFERENCES and order.order_status!='CANCELLED','Commercial order identity/status does not permit an acknowledgement')
    require(not order.shopify_total_refunded or Decimal(order.shopify_total_refunded)==0,'Observed Shopify refund requires review; no provisional acknowledgement')
    require(order.shopify_financial_status not in {'REFUNDED','PARTIALLY_REFUNDED','VOIDED'},'Observed Shopify refund/void requires review')
    require(not db.query(POSRefund).filter_by(checkout_id=sale.id).first() and not db.query(POSOperation).filter_by(checkout_id=sale.id,kind='REFUND').first(),'Refund activity requires separate review; no provisional acknowledgement')
    rows=db.query(SalesPayment).filter_by(sales_order_id=sale.sales_order_id).all()
    tenders=sale.frozen['tenders'];by_key={p.idempotency_key:p for p in rows}
    require(len(rows)==len(tenders) and len(by_key)==len(rows),'Tender evidence is incomplete or ambiguous')
    included=[];total=Decimal(0)
    for index,tender in enumerate(tenders):
        payment=by_key.get('pos:'+sale.id+':'+str(index))
        require(payment and payment.source=='POS' and payment.currency=='LKR' and payment.payment_method==tender['method'] and Decimal(payment.amount)==Decimal(tender['amount']) and (payment.payment_reference or '')==(tender.get('reference') or ''),'Payment evidence does not match the original tender')
        require(payment.verification_status in {'PENDING','VERIFIED'} and not payment.rejected_at,'Rejected or invalid payment cannot receive an acknowledgement')
        if payment.verification_status!='VERIFIED':continue
        require(payment.verified_at is not None and bool(payment.verified_by) and Decimal(payment.amount)>0,'Verified receipt evidence is incomplete')
        require(tender['method'] in METHODS,'Unsupported tender evidence')
        if tender['method'] in {'CASH','CARD_PAYMENT'}:require(tender.get('confirmed') is True,'Actual cash/terminal receipt was not confirmed')
        if tender['method']=='CASH':
            require(Decimal(tender['received'])-Decimal(tender['change'])==Decimal(payment.amount) and Decimal(tender['change'])>=0,'Frozen cash received/change is inconsistent')
        included.append({'id':payment.id,'method':payment.payment_method,'amount':str(payment.amount),'verified_at':payment.verified_at.isoformat(),'tender':tender})
        total+=Decimal(payment.amount)
    grand=Decimal(sale.frozen['quote']['grand_total'])
    require(included and Decimal(0)<total<=grand,'A positive, genuinely received and verified payment is required')
    return included,total,grand-total


class ProvisionalLayout(ReceiptLayout):
    def draw_acknowledgement(self,sale,meta,paid,total,balance):
        f=sale.frozen;q=f['quote'];policy=f['policy']
        self.pdf.set_title('PixiePinks invoice — provisional, final confirmation pending')
        if LOGO.is_file():
            try:
                y=self.pdf.get_y()
                self.pdf.image(str(LOGO),x=(self.pdf.w-32)/2,y=y,w=32,h=32,keep_aspect_ratio=True)
                self.pdf.set_y(y+33)
            except (OSError,ValueError):self.text('PixiePinks',17,'C')
        else:self.text('PixiePinks',17,'C')
        for contact in CONTACTS:self.text(contact,8,'C')
        self.text('INVOICE',15,'C')
        self.text('PROVISIONAL — FINAL CONFIRMATION PENDING',8,'C')
        if meta['duplicate'] or meta['revision']>1:self.text('COPY / REPRINT',8,'C')
        self.rule()
        self.text('Sale recorded: '+local_time(sale.created_at),8)
        self.text('Cashier: '+f['cashier'],8)
        self.text('Customer: '+(f.get('customer_name') or 'Walk-in Customer'),8)
        self.text('Invoice No: '+sale.reference,9)
        self.rule();self.text('ITEM / QTY × UNIT PRICE',8);self.text('All amounts in LKR',7,'R')
        for line in q['lines']:
            self.text(line['title'],9.5)
            if line.get('variant_title') and line['variant_title']!='Default Title':self.text('Variant: '+line['variant_title'],8)
            self.pair(str(line['quantity'])+' × '+lkr(line['unit_price']),lkr(line['total']))
            if Decimal(line['line_discount']):self.pair('Item discount','−'+lkr(line['line_discount']),8)
            if Decimal(line['order_discount']):self.pair('Order discount share','−'+lkr(line['order_discount']),8)
            self.pdf.set_y(self.pdf.get_y()+1.5)
        self.rule();self.pair('Subtotal',lkr(q['subtotal']))
        self.pair('Total discount',lkr(q['discount_total']))
        if q['tax_total'] is not None:self.pair(policy.get('tax_title') or 'Tax',lkr(q['tax_total']))
        self.pair('GRAND TOTAL LKR',lkr(q['grand_total']),12)
        self.rule();self.text('VERIFIED PAYMENTS ACTUALLY RECEIVED',9)
        for payment in paid:
            tender=payment['tender'];self.pair(METHODS[payment['method']],lkr(payment['amount']))
            if payment['method']=='CASH':
                self.pair('Cash received',lkr(tender['received']),8);self.pair('Change',lkr(tender['change']),8)
        self.pair('Verified received LKR',lkr(total),11)
        self.pair('Remaining balance LKR',lkr(balance),10)
        if balance:self.text('Pending/unverified tender claims are NOT acknowledged as received.',8)
        self.rule()
        methods=list(dict.fromkeys(PAYMENT_LABELS[payment['method']] for payment in paid))
        self.text('Mode of Payment: '+('Mixed — ' if len(methods)>1 else '')+' / '.join(methods),9)
        self.text('No of Item sold: '+str(sum(line['quantity'] for line in q['lines'])),9)
        self.rule()
        self.text('Items in Original Condition can be Exchanged',8,'C')
        self.text('within 3 days along with the receipt.',8,'C')
        self.text('All purchases are non-refundable.',8,'C')
        self.text('No cash refund.',8,'C')
        self.rule();self.text('Thank you for shopping @ Pixiepinks',9,'C')
        self.rule();self.text('Powered by PRYAMO',8,'C');self.text('0703322111',8,'C')
        return self.pdf.get_y()+5


def render(sale,meta,paid,total,balance):
    measure=ProvisionalLayout('80',10000)
    height=ceil(measure.draw_acknowledgement(sale,meta,paid,total,balance)*10)/10
    require(height<=1500,'Provisional document exceeds supported roll length; reduce neither content nor amounts')
    layout=ProvisionalLayout('80',height);layout.draw_acknowledgement(sale,meta,paid,total,balance)
    return bytes(layout.pdf.output())


def generate(db,user,checkout_id,confirm_received):
    service.require(user,'sell')
    if confirm_received is not True:raise HTTPException(422,'Explicit confirmation of genuinely received funds is required')
    service.locked(db);sale=service.sale(db,user,checkout_id)
    paid,total,balance=evidence(db,sale)
    previous=db.query(POSAudit).filter(POSAudit.shop==sale.shop,POSAudit.reference==sale.id,POSAudit.action.in_(EVENTS)).order_by(POSAudit.id.desc()).first()
    issued=datetime.utcnow();fingerprint=service.digest(paid)
    old=previous.detail if previous else None
    revision=old['revision'] if old else 1
    duplicate=bool(old and old['payment_evidence_hash']==fingerprint)
    if old and not duplicate:revision+=1
    first=datetime.fromisoformat(old['first_issued_at']) if old else issued
    meta={'document_id':'PPA-'+sale.reference,'revision':revision,'duplicate':duplicate,'issued_at':issued,'first_issued_at':first}
    data=render(sale,meta,paid,total,balance)
    service.audit(db,user,sale.id,'PROVISIONAL_REPRINTED' if duplicate else 'PROVISIONAL_UPDATED' if old else 'PROVISIONAL_ISSUED',document_id=meta['document_id'],revision=revision,first_issued_at=first.isoformat(),issued_at=issued.isoformat(),payment_evidence_hash=fingerprint,payment_ids=[p['id'] for p in paid],verified_total=str(total),balance=str(balance),frozen_hash=sale.frozen_hash,genuine_receipt_confirmed=True)
    db.commit()
    return data


def availability(db,sale):
    """Read-only UI hint. Generation always rechecks under the worker lock."""
    try:
        evidence(db,sale)
        return {'allowed':True,'reason':'Payment acknowledgement only; checkout processing and final receipt remain pending.'}
    except HTTPException as exc:return {'allowed':False,'reason':exc.detail}
    except (KeyError,ValueError,TypeError):return {'allowed':False,'reason':'Payment/snapshot evidence is unavailable; administrator review required.'}
