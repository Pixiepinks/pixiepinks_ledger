"""Browser/PDF receipts; only confirmed financial outcomes get final documents."""
import io,textwrap
from pathlib import Path
from datetime import timezone
from zoneinfo import ZoneInfo
from decimal import Decimal
from fastapi import HTTPException
from fpdf import FPDF
from math import ceil
from pos_models import POSRefund,POSAudit
import pos_service as service


def generate(db,user,checkout_id,profile='80',refund_id=None):
    service.require(user);service.locked(db);r=service.sale(db,user,checkout_id)
    if profile not in {'58','80','A4'}:raise HTTPException(422,'Choose 58, 80 or A4 receipt')
    refund=db.get(POSRefund,refund_id) if refund_id else None
    if refund_id and (not refund or refund.checkout_id!=r.id or refund.status!='PAID_OUT'):raise HTTPException(409,'Verified refund and confirmed payout required')
    if not refund and r.status!='COMPLETE':raise HTTPException(409,'Shopify completion uncertain/unpaid: no finalized receipt can be issued')
    ref=refund.id if refund else r.id
    previous=db.query(POSAudit).filter_by(shop=r.shop,reference=ref,action='RECEIPT_GENERATED').count()
    service.audit(db,user,ref,'RECEIPT_GENERATED',duplicate=previous>0,profile=profile,refund=bool(refund));db.commit()
    policy=r.frozen['policy'];q=r.frozen['quote'];rows=[]
    for line in q['lines']:
        rows.extend([line['title']+' · '+line['variant_title'],str(line['quantity'])+' x '+line['unit_price']+'    '+line['total'],
            'Discount '+service.calc.fmt(Decimal(line['line_discount'])+Decimal(line['order_discount']))])
    if refund:
        rows=['Original sale '+r.reference,'Refund '+refund.id,'Reason: '+refund.plan['reason']]
        for item in refund.plan['items']:rows.extend([q['lines'][item['line']]['title'],str(item['quantity'])+' returned · '+item['amount']])
    width=210 if profile=='A4' else int(profile)
    font_dir=Path(__file__).parent/'static'/'retail-fonts'
    pdf=FPDF(unit='mm',format=(width,297 if profile=='A4' else max(200,130+len(rows)*14)))
    pdf.set_margins(8 if profile=='A4' else 4,8,8 if profile=='A4' else 4)
    pdf.set_auto_page_break(True,margin=12)
    pdf.add_font('Noto',fname=str(font_dir/'NotoSans-Regular.ttf'));pdf.add_font('Sinhala',fname=str(font_dir/'NotoSansSinhala-Regular.ttf'))
    pdf.set_font('Noto',size=9);pdf.set_fallback_fonts(['Sinhala']);pdf.set_text_shaping(True);pdf.add_page()
    def line(text,size=9,bold=False):
        pdf.set_font('Noto',size=size)
        pdf.multi_cell(w=0,h=size*.48,text=str(text),new_x='LMARGIN',new_y='NEXT')
    logo=Path(__file__).parent/'static'/'pwa-icon-192.png'
    if logo.is_file():
        try:pdf.image(str(logo),x=pdf.l_margin,y=pdf.get_y(),w=14,h=14);pdf.set_y(pdf.get_y()+18)
        except Exception:pass
    line('PixiePinks',15,True);line('REFUND RECEIPT' if refund else 'SALES RECEIPT',10,True)
    if previous:line('DUPLICATE / REPRINT',10,True)
    if policy['shop_contact']:line(policy['shop_contact'])
    if policy['legal_id']:line('Legal/tax ID: '+policy['legal_id'])
    line(r.reference,10,True);line((refund.payout_at if refund else r.created_at).replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Asia/Colombo')).strftime('%Y-%m-%d %H:%M Sri Lanka'))
    line('Cashier: '+r.frozen['cashier']);line('Customer: '+r.frozen['customer_name'])
    if r.frozen['customer_phone']:line('Contact: '+r.frozen['customer_phone'])
    if r.frozen['cart'].get('invoice_details'):line('Invoice details: '+r.frozen['cart']['invoice_details'])
    line('-'*26)
    for row in rows:line(row)
    line('-'*26)
    if refund:
        line('REFUND LKR '+refund.plan['amount'],12,True);line('Paid out: '+refund.plan['method']);line('Restock handled by Shopify: '+str(refund.plan['restock']))
    else:
        for name,key in [('Subtotal','subtotal'),('Discounts','discount_total'),('Tax','tax_total'),('TOTAL LKR','grand_total')]:line(name+' '+str(q[key]),11,key=='grand_total')
        for t in r.frozen['tenders']:line(t['method']+' '+t['amount']+' · received '+t['received']+' · change '+t['change'])
        line('Payment: PAID / Shopify verified',9,True)
    line('Thank you for shopping with PixiePinks.')
    data=bytes(pdf.output())
    if profile!='A4' and pdf.page_no()==1:
        from pypdf import PdfReader,PdfWriter
        reader=PdfReader(io.BytesIO(data));page=reader.pages[0]
        bottom=max(0,float(page.mediabox.height)-max(80,pdf.get_y()+8)*pdf.k)
        page.mediabox.lower_left=(0,bottom);page.cropbox=page.mediabox
        writer=PdfWriter();writer.add_page(page);stream=io.BytesIO();writer.write(stream);data=stream.getvalue()
    return data
