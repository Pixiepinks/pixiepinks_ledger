"""Finalized snapshot PDF tests; all external/hardware operations are mocked."""
import copy,io
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
import pytest
from pypdf import PdfReader
import pypdfium2 as pdfium
from fastapi import HTTPException
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail
from test_inventory_phase3 import inv
from test_pos import pos,checkout,cash
from test_hardware import hardware,finish_sale
from models import User,JournalEntry,SalesPayment
from pos_models import POSCheckout,POSAudit,POSRefund
from hardware_models import HardwareJob
from settings import settings
import pos_receipts as receipts
import pos_service as service
import pos_money as calc
import hardware_service as hardware_service


def sample(lines=1,title='Kids Projector Torch',variant='Pink',tenders=None):
    items=[{'variant_id':str(i),'title':title,'variant_title':variant,'quantity':i%3+1,'unit_price':'1500.00','taxable':True,'discount':{'type':'FIXED','value':'0.00'}} for i in range(lines)]
    quote=calc.calculate(items,service.ZERO,'NONE',Decimal(0))
    return SimpleNamespace(reference='POS-20261120-000123',created_at=datetime(2026,11,20,6,30),frozen={'quote':quote,'policy':{**service.DEFAULT,'shop_contact':'Magammana West\n011 123 4567'},'cashier':'cashier-one','customer_name':'Walk-in Customer','customer_phone':'','cart':{},'tenders':tenders or [{'method':'CASH','amount':quote['grand_total'],'received':quote['grand_total'],'change':'0.00','reference':''}]})


def text(raw):return '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(raw)).pages)


def assert_geometry(raw,width):
    reader=PdfReader(io.BytesIO(raw));assert all(abs(float(p.mediabox.width)*25.4/72-width)<.01 for p in reader.pages)
    document=pdfium.PdfDocument(raw)
    for page in document:
        tp=page.get_textpage();w,h=page.get_size()
        assert tp.count_chars()>0
        for index in range(tp.count_chars()):
            left,bottom,right,top=tp.get_charbox(index)
            assert left>=-.1 and right<=w+.1 and bottom>=-.1 and top<=h+.1
        tp.close();page.close()
    document.close()
    return reader


@pytest.mark.parametrize('profile,width',[('80',80),('58',58),('A4',210)])
def test_dimensions_wrapping_and_no_blank_trailing_page(profile,width):
    short=receipts.render(sample(),profile)
    long=receipts.render(sample(20,title='Magical Water Book with many reusable pages and learning activities '*3,variant='Large / ocean animals / gift pack'),profile)
    a=assert_geometry(short,width);b=assert_geometry(long,width)
    assert len(a.pages)==1 and all(p.extract_text().strip() for p in b.pages)
    assert 'Thank you' in b.pages[-1].extract_text()
    if profile!='A4':
        assert len(b.pages)==1 and float(b.pages[0].mediabox.height)>float(a.pages[0].mediabox.height)
        assert tuple(b.pages[0].mediabox.lower_left)==(0,0)
    else:assert len(b.pages)>1


def test_discount_tax_tenders_customer_and_frozen_fields():
    sale=sample(2);items=sale.frozen['quote']['lines']
    items[0]['quantity']=2;items[0]['discount']={'type':'FIXED','value':'300.00'};items[1]['quantity']=1;items[1]['unit_price']='500.00'
    quote=calc.calculate(items,{'type':'FIXED','value':'200.00'},'EXCLUSIVE',Decimal('18'))
    sale.frozen.update(quote=quote,customer_name='Nimali Perera',customer_phone='94771234567',tenders=[{'method':'CASH','amount':'3540.00','received':'4000.00','change':'460.00','reference':''}])
    sale.frozen['policy'].update(tax_title='VAT 18%',legal_id='TEST-REGISTRATION')
    before=copy.deepcopy(sale.frozen);output=text(receipts.render(sale,'80'))
    for expected in ['3,500.00','300.00','200.00','500.00','540.00','3,540.00','4,000.00','460.00','VAT 18%','Item discount','Order discount share','Total discount','Nimali Perera','94771234567','TEST-REGISTRATION','2026-11-20 12:00 Sri Lanka','cashier-one']:
        assert expected in output
    assert sale.frozen==before


@pytest.mark.parametrize('methods',[['CASH'],['CARD_PAYMENT'],['BANK_TRANSFER'],['CASH','CARD_PAYMENT','BANK_TRANSFER']])
def test_payment_methods_only_show_cash_received_and_change_for_cash(methods):
    tenders=[{'method':method,'amount':'500.00','received':'600.00' if method=='CASH' else '500.00','change':'100.00' if method=='CASH' else '0.00','reference':'EXTERNAL-REFERENCE' if method!='CASH' else ''} for method in methods]
    output=text(receipts.render(sample(tenders=tenders),'80'))
    for method in methods:assert receipts.METHODS[method]+' applied' in output
    assert ('Cash received' in output)==('CASH' in methods)
    assert ('Change' in output)==('CASH' in methods)
    assert 'PAID / Shopify verified' in output
    assert 'Walk-in Customer' in output and 'Legal/tax ID' not in output


def test_sinhala_fonts_embedded_and_long_unbroken_names_fit():
    raw=receipts.render(sample(title='ළමා පොත් '+('LongProductName'*12),variant='රෝස පාට'),'80')
    assert_geometry(raw,80)
    reader=PdfReader(io.BytesIO(raw));fonts=reader.pages[0]['/Resources']['/Font']
    assert any('Sinhala' in str(font.get_object().get('/BaseFont')) for font in fonts.values())
    assert '\ufffd' not in text(raw)


@pytest.mark.parametrize('status',['QUEUED','AWAITING_PAYMENT','PAYMENT_READY','FAILED','CANCELLED','REVIEW','UNCERTAIN'])
def test_nonfinal_states_have_no_receipt_or_audit(pos,status):
    r=pos;rid=checkout(r)
    # Read-only receipt guard is tested with a detached source model; no invalid
    # production state transition or immutable financial snapshot is rewritten.
    with r.sessions() as db:
        row=SimpleNamespace(status=status)
        from unittest.mock import patch
        with patch.object(service,'sale',return_value=row):
            with pytest.raises(HTTPException) as failure:receipts.generate(db,db.get(User,r.uid),rid)
        assert failure.value.status_code==409
        assert db.query(POSAudit).filter_by(action='RECEIPT_GENERATED').count()==0


def test_browser_and_hardware_share_content_audit_and_duplicate_semantics(hardware,monkeypatch):
    r=hardware;monkeypatch.setattr(settings,'POS_CASH_DRAWER_AUTOMATION_ENABLED',False);rid=finish_sale(r)
    with r.sessions() as db:before=(db.query(JournalEntry).count(),db.query(SalesPayment).count());frozen=copy.deepcopy(db.get(POSCheckout,rid).frozen)
    response=r.client.post('/crm/pos/api/sales/'+rid+'/receipt/pdf',headers={'X-CSRF-Token':r.csrf},json={'profile':'80','refund_id':None})
    assert response.status_code==200 and response.headers['content-type']=='application/pdf'
    first=text(response.content);assert 'DUPLICATE' not in first
    with r.sessions() as db:
        user=db.get(User,r.uid);job=hardware_service.receipt_job(db,user,r.did,rid,'auto','80',True);db.commit()
        second=text(job.pdf);assert 'DUPLICATE / REPRINT' in second
        assert second.replace('DUPLICATE / REPRINT\n','')==first
        audit=db.query(POSAudit).filter_by(reference=rid,action='RECEIPT_GENERATED').all()
        assert [a.detail['duplicate'] for a in audit]==[False,True]
        assert db.query(HardwareJob).filter_by(kind='DRAWER').count()==0
        assert (db.query(JournalEntry).count(),db.query(SalesPayment).count())==before
        assert db.get(POSCheckout,rid).frozen==frozen
    assert len(r.pos.calls)==1 and not r.remote.mutations


def test_render_failure_does_not_consume_original_audit(pos,monkeypatch):
    r=pos;rid=checkout(r);service.dispatch_one(r.sessions,r.pos)
    monkeypatch.setattr(receipts,'render',lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('render unavailable')))
    with r.sessions() as db:
        with pytest.raises(ValueError):receipts.generate(db,db.get(User,r.uid),rid)
        assert db.query(POSAudit).filter_by(action='RECEIPT_GENERATED').count()==0


def test_refund_requires_verified_payout_and_stays_distinct(pos):
    r=pos;rid=checkout(r);service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        u=db.get(User,r.uid);refund=service.prepare_refund(db,u,rid,r.sid,uuid4().hex,[{'line':0,'quantity':1}],'CASH',False,'Returned item');fid=refund.id
    service.dispatch_one(r.sessions,r.pos)
    with r.sessions() as db:
        u=db.get(User,r.uid)
        with pytest.raises(HTTPException):receipts.generate(db,u,rid,refund_id=fid)
        service.confirm_payout(db,u,fid,'Cash handed back')
        raw=receipts.generate(db,u,rid,refund_id=fid);output=text(raw)
        assert 'REFUND RECEIPT' in output and 'Refund payout: CONFIRMED' in output
        assert '8,500.00' in output and 'Payment: PAID' not in output
        assert 'DUPLICATE' in text(receipts.generate(db,u,rid,refund_id=fid))
