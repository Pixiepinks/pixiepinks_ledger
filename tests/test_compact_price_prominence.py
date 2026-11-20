"""Actual 203 dpi glyph bounds/decoding and exact old-profile rendering parity."""
import copy,io,json,hashlib
from pathlib import Path
from decimal import Decimal
from math import floor
import pytest
from pypdf import PdfReader
from pypdf.generic import ContentStream
import pypdfium2 as pdfium
import zxingcpp
import retail_labels as labels
from test_compact_sku_price_label import example,config


@pytest.fixture(scope='module')
def previous():
    return json.loads((Path(__file__).parent/'fixtures/compact_price_baseline.json').read_text())


def bars(raw):
    reader=PdfReader(io.BytesIO(raw))
    return [list(operands) for operands,op in ContentStream(reader.pages[0].get_contents(),reader).operations if op==b're']


@pytest.mark.parametrize('price',['350.00','1860.00','15500.00','125000.00','123456789.00'])
@pytest.mark.parametrize('margin',[.5,.625,.75])
def test_largest_safe_bold_price_actual_bounds_and_scanning(price,margin,previous):
    c=config();c['margin_mm']=margin;item=example(price=price)
    raw=labels.backend.render([item],c)
    reader=PdfReader(io.BytesIO(raw));page=reader.pages[0]
    assert float(page.mediabox.width)*25.4/72==pytest.approx(30,abs=.01)
    assert float(page.mediabox.height)*25.4/72==pytest.approx(15,abs=.01)
    price_text='Rs. '+format(Decimal(price),',.2f')
    assert page.extract_text().splitlines()==[item.title,'P2 | SKU: 1001150',price_text]
    assert bars(raw)==previous['compact'][str(margin)]['bars']
    fonts=[]
    page.extract_text(visitor_text=lambda text,cm,tm,font,size:fonts.append((text.strip(),size,str(font.get('/BaseFont')))) if text.strip() else None)
    value=next(row for row in fonts if row[0]==price_text)
    assert all('Bold' not in font for text,size,font in fonts if text!=price_text)
    assert value[1]>5 and 'Bold' in value[2]
    doc=pdfium.PdfDocument(raw);tp=doc[0].get_textpage();h=doc[0].get_size()[1]
    def bound(text):
        search=tp.search(text);start,count=search.get_next();search.close()
        boxes=[tp.get_charbox(i) for i in range(start,start+count) if tp.get_text_range(i,1).strip()]
        return min(b[0] for b in boxes)*25.4/72,(h-max(b[3] for b in boxes))*25.4/72,max(b[2] for b in boxes)*25.4/72,(h-min(b[1] for b in boxes))*25.4/72
    left,top,right,bottom=bound(price_text);identifier=bound('P2 | SKU: 1001150')
    assert top>=identifier[3]+labels.DOT-.02
    assert bottom<=15-margin+.02 and left>=margin-.02 and right<=30-margin+.02
    assert abs((left+right)/2-15)<.15
    # Largest 1/100-pt choice: a larger candidate must violate width/ink/grid clearance.
    pdf=labels.label_pdf(c);plan=labels.compact_price_plan(pdf,price_text,c)
    assert plan.font_pt==pytest.approx(value[1],abs=.01)
    assert plan.ink_top>=identifier[3]+labels.DOT
    units,bounds=labels.price_font_metrics()
    asc=max(bounds[c][3] for c in price_text if c in bounds);desc=min(bounds[c][1] for c in price_text if c in bounds)
    next_size=plan.font_pt+.01;scale=next_size*25.4/72/units
    bottom=15-margin-labels.DOT
    best_baseline=floor((bottom+desc*scale)/labels.DOT)*labels.DOT
    name_height=5*.352778*1.2;id_height=4.5*.352778*1.2
    safe_top=round((margin+name_height+.5)/labels.DOT)*labels.DOT+5+.45+id_height+2*labels.DOT
    assert plan.width*next_size/plan.font_pt>30-2*margin-2*labels.DOT or best_baseline-asc*scale<safe_top
    image=doc[0].render(scale=203/72).to_pil()
    codes=zxingcpp.read_barcodes(image)
    assert len(codes)==1 and codes[0].text=='P2' and codes[0].format==zxingcpp.BarcodeFormat.Code128
    assert 12<=round(plan.font_pt*203/72)<=45 # legible true-size raster, no rescaling trick
    tp.close();doc.close()


def test_other_profiles_have_identical_drawing_stream_and_203dpi_pixels(previous):
    for key,c in labels.PRESETS.items():
        if key==labels.COMPACT_SKU_PRICE:continue
        item=example(title='Product',variant='Blue')
        current=labels.backend.render([item],copy.deepcopy(c));baseline=previous['other_profiles'][key]
        a=PdfReader(io.BytesIO(current));assert list(a.pages[0].mediabox)==baseline['media_box']
        assert hashlib.sha256(a.pages[0].get_contents().get_data()).hexdigest()==baseline['stream_sha256']
        x=pdfium.PdfDocument(current)
        assert hashlib.sha256(x[0].render(scale=203/72).to_pil().tobytes()).hexdigest()==baseline['raster203_sha256']
        x.close()


def test_name_identifier_geometry_unchanged_and_price_adapts(previous):
    c=config();regular=[]
    for value in ['350.00','1860.00','15500.00','125000.00','123456789.00']:
        item=example(price=value);raw=labels.backend.render([item],c)
        for text in [item.title,'P2 | SKU: 1001150']:
            def position(data):
                result=[]
                PdfReader(io.BytesIO(data)).pages[0].extract_text(visitor_text=lambda value,cm,tm,font,size:result.append([tm,size]) if value.strip()==text else None)
                return result
            assert position(raw)==[previous['compact']['0.625']['positions'][text]]
        plan=labels.compact_price_plan(labels.label_pdf(c),'Rs. '+format(Decimal(value),',.2f'),c);regular.append(plan.font_pt)
    assert regular[-1]<regular[0]


def test_comparison_pdf_is_true_size_and_all_eight_barcodes_decode():
    path=Path(__file__).parents[1]/'docs/evidence/compact-price-comparison.pdf'
    reader=PdfReader(path);doc=pdfium.PdfDocument(path)
    assert len(reader.pages)==len(doc)==8
    for index,page in enumerate(reader.pages):
        assert float(page.mediabox.width)*25.4/72==pytest.approx(30,abs=.01)
        assert float(page.mediabox.height)*25.4/72==pytest.approx(15,abs=.01)
        codes=zxingcpp.read_barcodes(doc[index].render(scale=203/72).to_pil())
        assert len(codes)==1 and codes[0].text=='S1'
        value=[]
        page.extract_text(visitor_text=lambda text,cm,tm,font,size:value.append((size,str(font.get('/BaseFont')))) if text.strip().startswith('Rs. ') else None)
        assert len(value)==1
        if index%2:assert value[0][0]>10 and 'Bold' in value[0][1]
        else:assert value[0][0]==5 and 'Bold' not in value[0][1]
    doc.close()
