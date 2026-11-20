"""Canonical finalized receipt PDF for browser and authorized hardware jobs."""
from pathlib import Path
from datetime import timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from math import ceil
from fastapi import HTTPException
from fpdf import FPDF
from pos_models import POSRefund,POSAudit
import pos_service as service

FONTS=Path(__file__).parent/'static'/'retail-fonts'
METHODS={'CASH':'Cash','CARD_PAYMENT':'Card payment','BANK_TRANSFER':'Bank transfer'}


def lkr(value):
    """Format frozen amounts; never recalculate prices, allocations or tax."""
    return format(Decimal(str(value)),',.2f')


def local_time(value):
    if value is None:return 'Not recorded'
    if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo('Asia/Colombo')).strftime('%Y-%m-%d %H:%M Sri Lanka')


class ReceiptLayout:
    def __init__(self,profile,height):
        self.pdf=pdf=FPDF(unit='mm',format=(210,297) if profile=='A4' else (int(profile),height))
        margin=12 if profile=='A4' else 4
        pdf.set_margins(margin,5,margin);pdf.set_auto_page_break(profile=='A4',margin=8)
        pdf.add_font('Noto',fname=str(FONTS/'NotoSans-Regular.ttf'))
        pdf.add_font('Sinhala',fname=str(FONTS/'NotoSansSinhala-Regular.ttf'))
        pdf.set_font('Noto',size=9);pdf.set_fallback_fonts(['Sinhala']);pdf.set_text_shaping(True)
        pdf.set_title('PixiePinks POS receipt');pdf.set_creator('PixiePinks POS');pdf.add_page()
        pdf.set_text_color(0);pdf.set_draw_color(0);pdf.set_line_width(.15)

    def text(self,value,size=9,align='L'):
        pdf=self.pdf;pdf.set_font('Noto',size=size);pdf.set_x(pdf.l_margin)
        pdf.multi_cell(w=0,h=size*.49,text=str(value),align=align,new_x='LMARGIN',new_y='NEXT')

    def rule(self):
        pdf=self.pdf
        if pdf.auto_page_break and pdf.get_y()+3>pdf.page_break_trigger:pdf.add_page()
        pdf.set_y(pdf.get_y()+1.5);pdf.line(pdf.l_margin,pdf.get_y(),pdf.w-pdf.r_margin,pdf.get_y());pdf.set_y(pdf.get_y()+2)

    def pair(self,label,value,size=9):
        pdf=self.pdf;pdf.set_font('Noto',size=size)
        usable=pdf.w-pdf.l_margin-pdf.r_margin;right=pdf.get_string_width(str(value))+1
        if right>usable:
            raise HTTPException(422,'Receipt amount cannot fit this paper width; choose A4')
        left=usable-right-2
        if left<16:
            self.text(label,size);self.text(value,size,'R');return
        height=pdf.multi_cell(w=left,h=size*.49,text=str(label),dry_run=True,output='HEIGHT')
        if pdf.auto_page_break and pdf.get_y()+height>pdf.page_break_trigger:pdf.add_page()
        y=pdf.get_y();pdf.set_x(pdf.l_margin)
        pdf.multi_cell(w=left,h=size*.49,text=str(label),new_x='LMARGIN',new_y='NEXT')
        pdf.set_xy(pdf.w-pdf.r_margin-right,y)
        pdf.cell(w=right,h=size*.49,text=str(value),align='R')
        pdf.set_xy(pdf.l_margin,y+height)

    def draw(self,sale,refund,duplicate):
        f=sale.frozen;q=f['quote'];policy=f['policy']
        self.text('PixiePinks',17,'C');self.text('REFUND RECEIPT' if refund else 'SALES RECEIPT',9,'C')
        if duplicate:self.text('DUPLICATE / REPRINT',10,'C')
        if policy.get('shop_contact'):self.text(policy['shop_contact'],8,'C')
        if policy.get('legal_id'):self.text('Legal/tax ID: '+policy['legal_id'],8,'C')
        self.rule();self.text('Sale / invoice: '+sale.reference,9)
        self.text(local_time(refund.payout_at if refund else sale.created_at),8)
        self.text('Cashier: '+f['cashier'],8)
        self.text('Customer: '+f['customer_name'],8)
        if f.get('customer_phone'):self.text('Phone: '+f['customer_phone'],8)
        if f['cart'].get('invoice_details'):self.text('Invoice details: '+f['cart']['invoice_details'],8)
        self.rule();self.text('ITEM / QTY × UNIT PRICE',8);self.text('All amounts in LKR',7,'R')
        if refund:
            self.text('Refund reference: '+refund.id,8)
            self.text('Reason: '+refund.plan['reason'],8)
            for item in refund.plan['items']:
                line=q['lines'][item['line']]
                self.text(line['title'],9.5)
                if line.get('variant_title') and line['variant_title']!='Default Title':self.text('Variant: '+line['variant_title'],8)
                self.pair(str(item['quantity'])+' returned',lkr(item['amount']))
            self.rule();self.pair('REFUND LKR',lkr(refund.plan['amount']),13)
            self.text('Paid out: '+METHODS.get(refund.plan['method'],refund.plan['method']),9)
            self.text('Refund payout: CONFIRMED',9)
            self.text('Restock handled by Shopify: '+str(refund.plan['restock']),8)
        else:
            for line in q['lines']:
                self.text(line['title'],9.5)
                if line.get('variant_title') and line['variant_title']!='Default Title':self.text('Variant: '+line['variant_title'],8)
                self.pair(str(line['quantity'])+' × '+lkr(line['unit_price']),lkr(line['total']))
                if Decimal(line['line_discount']):self.pair('Item discount','−'+lkr(line['line_discount']),8)
                if Decimal(line['order_discount']):self.pair('Order discount share','−'+lkr(line['order_discount']),8)
                self.pdf.set_y(self.pdf.get_y()+1.5)
            self.rule();self.pair('Subtotal',lkr(q['subtotal']))
            if Decimal(q['order_discount']):self.pair('Order discount (included)',lkr(q['order_discount']),8)
            self.pair('Total discount',lkr(q['discount_total']))
            tax_label=policy.get('tax_title') or 'Tax'
            if q['tax_total'] is not None:self.pair(tax_label,lkr(q['tax_total']))
            else:self.text(tax_label+': not configured',8)
            self.rule();self.pair('TOTAL LKR',lkr(q['grand_total']),13);self.rule()
            for tender in f['tenders']:
                method=tender['method'];self.pair(METHODS.get(method,method)+' applied',lkr(tender['amount']))
                if method=='CASH':
                    self.pair('Cash received',lkr(tender['received']),8)
                    self.pair('Change',lkr(tender['change']),9)
                if tender.get('reference'):self.text('Payment reference: '+tender['reference'],8)
            self.text('Payment: PAID / Shopify verified',8,'C')
        self.rule();self.text('Thank you for shopping with PixiePinks.',8,'C')
        return self.pdf.get_y()+5


def render(sale,profile,refund=None,duplicate=False):
    if profile=='A4':
        layout=ReceiptLayout(profile,297);layout.draw(sale,refund,duplicate)
    else:
        # Same measured layout on both passes: wrap by actual Unicode font metrics,
        # then create a true zero-origin custom page, without a crop/scale transform.
        measure=ReceiptLayout(profile,10000);height=ceil(measure.draw(sale,refund,duplicate)*10)/10
        if height>1500:raise HTTPException(422,'Receipt exceeds supported roll length; choose A4')
        layout=ReceiptLayout(profile,height);layout.draw(sale,refund,duplicate)
    return bytes(layout.pdf.output())


def generate(db,user,checkout_id,profile='80',refund_id=None,*,commit=True,duplicate=False):
    service.require(user);service.locked(db);r=service.sale(db,user,checkout_id)
    if profile not in {'58','80','A4'}:raise HTTPException(422,'Choose 58, 80 or A4 receipt')
    refund=db.get(POSRefund,refund_id) if refund_id else None
    if refund_id and (not refund or refund.checkout_id!=r.id or refund.status!='PAID_OUT'):raise HTTPException(409,'Verified refund and confirmed payout required')
    if not refund and r.status!='COMPLETE':raise HTTPException(409,'Shopify completion uncertain/unpaid: no finalized receipt can be issued')
    ref=refund.id if refund else r.id
    previous=db.query(POSAudit).filter_by(shop=r.shop,reference=ref,action='RECEIPT_GENERATED').count()
    reprint=bool(previous or duplicate)
    data=render(r,profile,refund,reprint)
    # An unsuccessful render must not consume the original receipt/audit identity.
    service.audit(db,user,ref,'RECEIPT_GENERATED',duplicate=reprint,profile=profile,refund=bool(refund))
    db.commit() if commit else db.flush()
    return data
