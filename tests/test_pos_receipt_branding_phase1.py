"""Presentation-only phase 1; exact original asset and customer content."""
import copy,hashlib,io
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import pytest
from PIL import Image
from pypdf import PdfReader
from test_pos_receipts_phase4g import sample,assert_geometry,text
import pos_provisional_receipts as receipt


def document(methods=('CASH',),duplicate=False):
    sale=sample(2,title='Frozen water book',variant='Pink')
    sale.frozen['customer_name']=''
    total=Decimal(sale.frozen['quote']['grand_total'])
    paid=[]
    for index,method in enumerate(methods):
        amount=total/len(methods)
        paid.append({'id':index,'method':method,'amount':str(amount),'tender':{'received':str(amount+100),'change':'100'}})
    meta={'document_id':'PPA-'+sale.reference,'revision':2 if duplicate else 1,'duplicate':duplicate,'issued_at':datetime(2026,11,20,12),'first_issued_at':datetime(2026,11,20,11)}
    before=copy.deepcopy(sale.frozen)
    raw=receipt.render(sale,meta,paid,total,Decimal(0))
    assert sale.frozen==before
    return sale,raw


def test_exact_original_fairy_is_embedded_with_rgb_and_transparency():
    assert hashlib.sha256(receipt.LOGO.read_bytes()).hexdigest()=='1e7a496e574b9f93d77fbc77b31c83a87e6b60c3634ff07a35c349bf9958685b'
    _,raw=document();reader=PdfReader(io.BytesIO(raw))
    images=[obj.get_object() for obj in reader.pages[0]['/Resources']['/XObject'].get_object().values() if obj.get_object().get('/Subtype')=='/Image']
    image=next(obj for obj in images if obj.get('/ColorSpace')=='/DeviceRGB')
    source=Image.open(receipt.LOGO)
    assert source.mode=='RGBA' and source.getextrema()[-1][0]==0
    assert (image['/Width'],image['/Height'])==source.size
    assert image.get_data()==source.convert('RGB').tobytes()
    assert image['/SMask'].get_object().get_data()==source.getchannel('A').tobytes()
    assert_geometry(raw,80)


def test_customer_text_order_reference_policy_and_footer():
    sale,raw=document();out=' '.join(text(raw).split())
    expected=[*receipt.CONTACTS,'INVOICE','PROVISIONAL — FINAL CONFIRMATION PENDING',
        'Sale recorded: 2026-11-20 12:00 Sri Lanka','Cashier: cashier-one','Customer: Walk-in Customer',
        'Invoice No: '+sale.reference,'ITEM / QTY × UNIT PRICE','Frozen water book','Variant: Pink',
        'Subtotal','Total discount','GRAND TOTAL LKR','Verified received LKR',
        'Mode of Payment: Cash','No of Item sold: 3',
        'Items in Original Condition can be Exchanged within 3 days along with the receipt.',
        'All purchases are non-refundable.','No cash refund.','Thank you for shopping @ Pixiepinks','Powered by PRYAMO','0703322111']
    cursor=0
    for value in expected:
        found=out.find(value,cursor);assert found>=0,value
        cursor=found+len(value)
    for removed in ['Acknowledgement:','Original POS reference:','First issued:','This copy issued:',
        'PROVISIONAL PAYMENT ACKNOWLEDGEMENT','NOT FINALIZED / NOT SHOPIFY VERIFIED','NOT A TAX INVOICE',
        'Shopify completion and final receipt remain pending.','Keep the original POS reference.']:
        assert removed not in out
    assert 'Legal/tax ID:' not in out


@pytest.mark.parametrize('methods,label',[
 (('CASH',),'Cash'),(('CARD_PAYMENT',),'Card Payment'),(('BANK_TRANSFER',),'Bank Transfer'),
 (('CASH','CARD_PAYMENT','BANK_TRANSFER'),'Mixed — Cash / Card Payment / Bank Transfer')])
def test_mode_lists_only_verified_methods(methods,label):
    _,raw=document(methods)
    assert 'Mode of Payment: '+label in ' '.join(text(raw).split())


def test_missing_logo_falls_back_to_store_name_and_reprint_is_visible(monkeypatch,tmp_path):
    monkeypatch.setattr(receipt,'LOGO',tmp_path/'missing.png')
    _,raw=document(duplicate=True);out=' '.join(text(raw).split())
    assert 'PixiePinks' in out and 'COPY / REPRINT' in out
    assert 'First issued:' not in out and 'revision 2' not in out
