"""Eight true-size synthetic labels: previous 5 pt / new adaptive bold per price.
Only writes review artifacts. Does not create a print job or access production.
"""
import io,json
from decimal import Decimal
from pathlib import Path
from pypdf import PdfReader,PdfWriter
import retail_labels as labels


def previous_pdf(item,c):
    # Preserve the pre-upgrade compact drawing routine for review samples only.
    pdf=labels.label_pdf(c);pdf.add_page()
    name,identifier,price,bits,module,width=labels.compact_plan(pdf,item,c)
    margin=c['margin_mm'];usable=30-2*margin
    name_height=5*.352778*1.2;id_height=4.5*.352778*1.2
    barcode_y=round((margin+name_height+.5)/labels.DOT)*labels.DOT
    def cell(text,size,top,height):
        pdf.set_font('Noto',size=size);pdf.set_xy(margin,top);pdf.cell(w=usable,h=height,text=text,align='C')
    cell(name,5,margin,name_height)
    left=round(((30-width)/2)/labels.DOT)*labels.DOT+10*module
    index=0
    while index<len(bits):
        if bits[index]=='0':index+=1;continue
        end=index+1
        while end<len(bits) and bits[end]=='1':end+=1
        pdf.rect(left+index*module,barcode_y,(end-index)*module,5,style='F');index=end
    cell(identifier,4.5,barcode_y+5+.45,id_height)
    cell(price,5,15-margin-name_height,name_height)
    return bytes(pdf.output())


def build(directory=Path('docs/evidence')):
    directory.mkdir(parents=True,exist_ok=True);writer=PdfWriter();rows=[]
    c=labels.PRESETS[labels.COMPACT_SKU_PRICE]
    for price in ['350.00','1860.00','15500.00','125000.00']:
        item=labels.Label('S1','SAMPLE Water Book',sku='SAMPLE001',price=price)
        plan=labels.compact_price_plan(labels.label_pdf(c),'Rs. '+format(Decimal(price),',.2f'),c)
        for mode,raw,size in [('Before',previous_pdf(item,c),5),('After',labels.backend.render([item],c),plan.font_pt)]:
            page=len(writer.pages);writer.add_page(PdfReader(io.BytesIO(raw)).pages[0])
            writer.add_outline_item(mode+' — Rs. '+format(Decimal(price),',.2f')+' — '+str(size)+' pt',page)
            rows.append({'page':page+1,'mode':mode,'price':price,'font_pt':size,'price_bold':mode=='After'})
    writer.add_metadata({'/Title':'SAMPLE — 30x15 compact price before/after; print at 100%','/Subject':'Eight 30×15 mm synthetic labels; odd pages old, even pages improved'})
    with (directory/'compact-price-comparison.pdf').open('wb') as output:writer.write(output)
    (directory/'compact-price-comparison.json').write_text(json.dumps(rows,indent=2)+'\n')
    return rows

if __name__=='__main__':print(json.dumps(build(),indent=2))
