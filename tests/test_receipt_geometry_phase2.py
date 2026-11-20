"""Actual PDF glyph, path/image and raster bounds for the 80 mm safe region."""
import copy,io
from datetime import datetime
from decimal import Decimal
import pytest
import pypdfium2 as pdfium
from pypdf import PdfReader
from PIL import Image
from test_pos_receipts_phase4g import sample,text
from pos_receipts import ReceiptLayout
import pos_provisional_receipts as receipts
from scripts.build_receipt_thermal_logo import build
MM=25.4/72


def render(lines=1,mixed=False,copy_document=False):
    sale=sample(lines,title='ළමා පොත් English water book with reusable pages '*4,variant='Pink / large gift pack')
    for index,line in enumerate(sale.frozen['quote']['lines']):line['title']='ITEM-'+str(index+1).zfill(2)+' '+line['title']
    sale.reference='POS-38FC859E30DBEAF49B86'
    total=Decimal(sale.frozen['quote']['grand_total'])
    meta={'document_id':'PPA-'+sale.reference,'revision':1,'duplicate':copy_document,'issued_at':datetime(2026,11,20,12),'first_issued_at':datetime(2026,11,20,12)}
    methods=['CASH','CARD_PAYMENT','BANK_TRANSFER'] if mixed else ['CASH']
    paid=[{'id':i+1,'method':method,'amount':str(total/len(methods)),'tender':{'received':str(total/len(methods)+500),'change':'500.00'}} for i,method in enumerate(methods)]
    frozen=copy.deepcopy(sale.frozen)
    data=receipts.render(sale,meta,paid,total,Decimal(0))
    assert sale.frozen==frozen
    return sale,data


def glyphs(raw):
    document=pdfium.PdfDocument(raw);page=document[0];tp=page.get_textpage()
    boxes=[tp.get_charbox(i) for i in range(tp.count_chars()) if tp.get_text_range(i,1).strip()]
    return document,page,tp,boxes


@pytest.mark.parametrize('lines',[1,3,12])
def test_all_glyphs_paths_and_image_inside_safe_region_and_footer_tail(lines):
    sale,raw=render(lines,copy_document=True)
    doc,page,tp,boxes=glyphs(raw);width,height=page.get_size()
    assert len(doc)==1 and abs(width*MM-80)<.01
    for left,bottom,right,top in boxes:
        assert left*MM>=5-.02 and right*MM<=75+.02
        assert bottom*MM>=4 and (height-top)*MM>=4-.02
    tail=min(b[1] for b in boxes)*MM
    assert 4<=tail<=6.5,tail
    out=' '.join(text(raw).split())
    for literal in [sale.reference,'PROVISIONAL — FINAL CONFIRMATION PENDING','COPY / REPRINT','0703322111']:
        assert literal in out
    images=[];paths=[]
    def operands(op,args,cm,tm):
        if op==b'Do':images.append(cm.copy())
        if op in {b'm',b'l'}:paths.append((float(args[0]),float(args[1])))
    PdfReader(io.BytesIO(raw)).pages[0].extract_text(visitor_operand_before=operands)
    assert images and all(abs((cm[4]+cm[0]/2)*MM-40)<.02 for cm in images)
    assert all(5<=x*MM<=75 for x,y in paths)
    assert all(4<=y*MM<=height*MM-4 for x,y in paths)
    tp.close();page.close();doc.close()


def test_money_right_edge_is_constant_and_labels_do_not_overlap():
    layout=ReceiptLayout('80',180);pdf=layout.pdf
    values=['350.00','0.00','12,345.67','999,999.99']
    for index,value in enumerate(values):layout.pair('Very long financial label that must wrap onto several readable lines',value,9)
    raw=bytes(pdf.output());doc,page,tp,boxes=glyphs(raw)
    rights=[]
    for value in values:
        search=tp.search(value,match_whole_word=True);match=search.get_next();assert match
        start,count=match;vb=[tp.get_charbox(i) for i in range(start,start+count)]
        rights.append(max(b[2] for b in vb)*MM)
        # No label glyph may touch the value's occupied band/column.
        low=min(b[1] for b in vb);high=max(b[3] for b in vb);left=min(b[0] for b in vb)
        for i in range(tp.count_chars()):
            if start<=i<start+count or not tp.get_text_range(i,1).strip():continue
            b=tp.get_charbox(i)
            if min(high,b[3])-max(low,b[1])>.5:assert b[2]<left-1
        search.close()
    assert max(rights)-min(rights)<.15 and all(73.5<x<74.5 for x in rights),rights
    assert abs(pdf.get_string_width('0000000000')-pdf.get_string_width('1111111111'))<.001
    tp.close();page.close();doc.close()


def test_exceptionally_large_value_wraps_without_clipping_or_lost_digits():
    layout=ReceiptLayout('80',200);value='1234567890'*8+'.00'
    layout.pair('Exceptional amount',value,12)
    raw=bytes(layout.pdf.output());doc,page,tp,boxes=glyphs(raw)
    out=text(raw);assert value in ''.join(out.split())
    assert all(b[0]*MM>=5-.02 and b[2]*MM<=75+.02 for b in boxes)
    tp.close();page.close();doc.close()


def test_dynamic_height_grows_and_two_passes_are_identical():
    sale,short=render(1);_,long=render(12)
    a=PdfReader(io.BytesIO(short));b=PdfReader(io.BytesIO(long))
    assert len(a.pages)==len(b.pages)==1 and float(b.pages[0].mediabox.height)>float(a.pages[0].mediabox.height)
    total=Decimal(sale.frozen['quote']['grand_total'])
    meta={'document_id':'PPA-'+sale.reference,'revision':1,'duplicate':False,'issued_at':datetime(2026,11,20,12),'first_issued_at':datetime(2026,11,20,12)}
    paid=[{'id':1,'method':'CASH','amount':str(total),'tender':{'received':str(total+500),'change':'500.00'}}]
    first=receipts.ProvisionalLayout('80',10000);end=first.draw_acknowledgement(sale,meta,paid,total,Decimal(0))
    second=receipts.ProvisionalLayout('80',float(a.pages[0].mediabox.height)*MM)
    assert abs(second.draw_acknowledgement(sale,meta,paid,total,Decimal(0))-end)<.001


def test_thermal_logo_deterministically_converts_original_and_centers_binary_art(tmp_path):
    original=receipts.LOGO.read_bytes();generated=build(target=tmp_path/'mono.png')
    with Image.open(generated) as generated_image,Image.open(receipts.THERMAL_LOGO) as shipped:
        assert shipped.mode=='1' and shipped.height==round(26*203/25.4)
        assert set(shipped.convert('L').getdata())=={0,255}
        assert generated_image.tobytes()==shipped.tobytes()
        assert abs(shipped.info['dpi'][0]-203)<.1
    assert receipts.LOGO.read_bytes()==original


def test_203dpi_raster_has_white_safe_margins_and_complete_footer():
    _,raw=render(2);doc=pdfium.PdfDocument(raw)
    raster=doc[0].render(scale=203/72).to_pil().convert('L')
    margin=int(5*203/25.4)-1
    assert raster.crop((0,0,margin,raster.height)).getextrema()==(255,255)
    assert raster.crop((raster.width-margin,0,raster.width,raster.height)).getextrema()==(255,255)
    assert raster.crop((0,raster.height-int(4*203/25.4),raster.width,raster.height)).getextrema()==(255,255)
    doc.close()


def test_centered_contacts_and_product_blocks_do_not_overlap():
    _,raw=render(3);doc,page,tp,boxes=glyphs(raw)
    for contact in receipts.CONTACTS:
        search=tp.search(contact);match=search.get_next();assert match
        start,count=match;bb=[tp.get_charbox(i) for i in range(start,start+count)]
        center=(min(b[0] for b in bb)+max(b[2] for b in bb))/2*MM
        assert abs(center-40)<.3,(contact,center)
        search.close()
    starts=[]
    for index in range(1,4):
        search=tp.search('ITEM-'+str(index).zfill(2));starts.append(search.get_next()[0]);search.close()
    for previous,current in zip(starts,starts[1:]):
        previous_boxes=[tp.get_charbox(i) for i in range(previous,current) if tp.get_text_range(i,1).strip()]
        current_top=max(tp.get_charbox(i)[3] for i in range(current,current+7))
        assert min(b[1] for b in previous_boxes)>current_top+.5
    tp.close();page.close();doc.close()


def test_long_reference_and_payment_reference_wrap_without_clipping():
    layout=ReceiptLayout('80',200);ref='POS-'+'ABC123'*12
    payment='BANK-'+'1234567890'*12
    layout.text('Invoice No:');layout.text(ref)
    layout.text('Payment reference: '+payment)
    raw=bytes(layout.pdf.output());doc,page,tp,boxes=glyphs(raw)
    out=''.join(text(raw).split())
    assert ref in out and payment in out
    assert all(b[0]*MM>=5-.02 and b[2]*MM<=75+.02 for b in boxes)
    tp.close();page.close();doc.close()


def test_mixed_payment_values_and_summary_stay_inside_safe_region():
    sale,raw=render(3,mixed=True)
    doc,page,tp,boxes=glyphs(raw)
    assert all(b[0]*MM>=5-.02 and b[2]*MM<=75+.02 for b in boxes)
    out=' '.join(text(raw).split())
    assert 'Mode of Payment: Mixed — Cash / Card Payment / Bank Transfer' in out
    assert 'Cash received' in out and 'Change' in out and 'Remaining balance LKR' in out
    assert len(doc)==1
    tp.close();page.close();doc.close()
