"""Driver PDF backend for 203-dpi Code 128 labels; no USB/TSPL/receipt output."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import unicodedata
from datetime import datetime,timedelta
from pathlib import Path
from math import floor
import hashlib
import os
import secrets
from typing import Protocol
from barcode import Code128
from fastapi import HTTPException
from fpdf import FPDF
from sqlalchemy import select
from retail_models import RetailVariant,RetailPrintJob,RetailLabelTemplate
from settings import settings
import retail_service as service

DOT=25.4/203
FONT_DIR=Path(__file__).resolve().parent/'static'/'retail-fonts'
MAX_PDF=32*1024*1024


def preset(width=50,height=30):
    compact=height<20
    return {'width_mm':width,'height_mm':height,'margin_mm':1,'module_dots':2,'barcode_height_mm':6 if compact else 10 if height<40 else 14,
        'font_pt':5 if compact else 6,'max_name_chars':60,'show_brand':not compact,'show_variant':not compact,
        'show_sku':height>=30,'show_price':False,'labels_per_row':1,'layout':'roll','gap_mm':2,
        'offset_x_mm':0,'offset_y_mm':0,'density':8,'calibration':'gap'}

PRESETS={f'{w}x{h}':preset(w,h) for w,h in [(25,15),(30,15),(40,20),(50,30),(60,40)]}
PRESETS['40x20']['barcode_height_mm']=7
COMPACT_SKU_PRICE='30x15-compact-sku-price'
COMPACT_LAYOUT='compact_sku_price'
PRESETS[COMPACT_SKU_PRICE]={**preset(30,15),'content_layout':COMPACT_LAYOUT,'margin_mm':.625,
    'barcode_height_mm':5,'show_sku':True,'show_price':True}
PRESET_TITLES={COMPACT_SKU_PRICE:'30x15 Compact — SKU & Price'}


def is_compact(config):return config.get('content_layout')==COMPACT_LAYOUT


def validate_config(config):
    if not isinstance(config,dict) or set(config) not in [set(preset()),set(preset())|{'content_layout'}]:raise HTTPException(422,'Provide all label profile settings')
    if 'content_layout' in config and config['content_layout']!=COMPACT_LAYOUT:raise HTTPException(422,'Unknown label content layout')
    limits={'width_mm':(20,210),'height_mm':(12,297),'margin_mm':(.5,10),'barcode_height_mm':(5,60),
        'font_pt':(5,14),'max_name_chars':(5,200),'labels_per_row':(1,8),'module_dots':(2,6),'gap_mm':(0,10),
        'offset_x_mm':(-2,2),'offset_y_mm':(-2,2),'density':(0,15)}
    for key,(low,high) in limits.items():
        value=config[key]
        if type(value) not in {int,float} or not low<=value<=high:raise HTTPException(422,f'Invalid {key}: expected {low}–{high}')
    for key in ['max_name_chars','labels_per_row','module_dots','density']:
        if type(config[key]) is not int:raise HTTPException(422,key+' must be a whole number')
    for key in ['show_brand','show_variant','show_sku','show_price']:
        if type(config[key]) is not bool:raise HTTPException(422,'Choose each label visibility option')
    if not isinstance(config['layout'],str) or not isinstance(config['calibration'],str) or config['layout'] not in {'roll','a4'} or config['calibration'] not in {'gap','continuous','black_mark'}:raise HTTPException(422,'Choose label layout and calibration mode')
    if config['layout']=='roll' and config['labels_per_row']*config['width_mm']>210:raise HTTPException(422,'Roll page width exceeds safety limit; confirm actual printer width')
    if config['layout']=='a4' and (config['labels_per_row']*config['width_mm']>210 or config['height_mm']>297):raise HTTPException(422,'Labels do not fit A4')
    if abs(config['offset_x_mm'])>=config['margin_mm'] or abs(config['offset_y_mm'])>=config['margin_mm']:raise HTTPException(422,'Calibration offsets must remain inside label margins')
    if is_compact(config):
        fixed={'width_mm':30,'height_mm':15,'font_pt':5,'barcode_height_mm':5,'show_brand':False,'show_variant':False,'show_sku':True,'show_price':True}
        if any(config[key]!=value for key,value in fixed.items()) or not .5<=config['margin_mm']<=.75:
            raise HTTPException(422,'30x15 Compact — SKU & Price requires 30×15 mm, 5 pt name/price, 5 mm barcode, SKU/price visible, no brand/variant line and 0.5–0.75 mm margins')
    return dict(config)


@dataclass(frozen=True)
class Label:
    barcode:str
    title:str
    variant:str=''
    sku:str=''
    price:str=''
    currency:str='LKR'

class LabelBackend(Protocol):
    def render(self,labels,config)->bytes: ...


def pattern(value):
    if not service.code_valid(value):raise HTTPException(422,'Barcode must be 1–100 printable ASCII characters, encoded exactly')
    return Code128(value).build()[0]


def symbol_geometry(value,config):
    bits=pattern(value);module=DOT*config['module_dots'];width=(len(bits)+20)*module
    if width>config['width_mm']-2*config['margin_mm']:
        raise HTTPException(422,f'Barcode {value} cannot fit this label at 203 dpi with 2+ dot modules and quiet zones. Choose a larger label; assigned values are never shortened.')
    return bits,module,width

def label_pdf(config):
    pdf=FPDF(unit='mm',format=(30,15))
    pdf.set_auto_page_break(False);pdf.set_margins(0,0,0)
    pdf.add_font('Noto',fname=str(FONT_DIR/'NotoSans-Regular.ttf'))
    pdf.add_font('Sinhala',fname=str(FONT_DIR/'NotoSansSinhala-Regular.ttf'))
    pdf.set_font('Noto',size=config['font_pt']);pdf.set_fallback_fonts(['Sinhala']);pdf.set_text_shaping(True)
    return pdf


def short_name(pdf,title,width,limit):
    if not isinstance(title,str) or not title.strip():raise HTTPException(422,'Compact label requires a product name')
    name=' '.join(title.split())
    # Keep combining marks, virama conjuncts and joiners attached when shortening.
    clusters=[];join=False
    for char in name:
        if clusters and (unicodedata.category(char).startswith('M') or char in '\u200c\u200d' or join):clusters[-1]+=char
        else:clusters.append(char)
        join=char=='\u200d' or unicodedata.combining(char)==9
    if len(name)<=limit and pdf.get_string_width(name)<=width:return name
    while clusters:
        candidate=''.join(clusters).rstrip()+'…'
        if len(candidate)<=limit and pdf.get_string_width(candidate)<=width:return candidate
        clusters.pop()
    raise HTTPException(422,'Product name cannot fit this compact label at 5 pt')


def compact_plan(pdf,label,config):
    if not isinstance(label.sku,str) or not label.sku.strip():raise HTTPException(422,'Compact label requires an SKU; assign or reconcile it before printing')
    if any(unicodedata.category(c) in {'Cc','Zl','Zp'} for c in label.sku):raise HTTPException(422,'SKU must fit on one line without control characters')
    if label.currency!='LKR':raise HTTPException(422,'Compact label requires the Shopify variant selling price in LKR')
    try:
        price=Decimal(label.price)
        if not price.is_finite() or price<0 or price.as_tuple().exponent < -2:raise InvalidOperation()
    except (InvalidOperation,TypeError,ValueError):raise HTTPException(422,'Compact label requires a valid variant selling price with at most two decimal places') from None
    bits,module,width=symbol_geometry(label.barcode,config)
    usable=config['width_mm']-2*config['margin_mm']
    pdf.set_font('Noto',size=5)
    name=short_name(pdf,label.title,usable,config['max_name_chars'])
    identifier=f'{label.barcode} | SKU: {label.sku}'
    pdf.set_font('Noto',size=4.5)
    if pdf.get_string_width(identifier)>usable:
        raise HTTPException(422,'Barcode number and SKU cannot fit together at 4.5 pt on 30×15 mm. Use a larger profile; identifiers are never truncated.')
    price_text=f'Rs. {price:,.2f}'
    pdf.set_font('Noto',size=5)
    if pdf.get_string_width(price_text)>usable:raise HTTPException(422,'Selling price cannot fit at 5 pt on 30×15 mm; use a larger profile')
    return name,identifier,price_text,bits,module,width


def draw_compact(pdf,label,config,x,y):
    name,identifier,price,bits,module,width=compact_plan(pdf,label,config)
    margin=config['margin_mm'];usable=config['width_mm']-2*margin
    name_height=5*.352778*1.2;id_height=4.5*.352778*1.2;price_height=name_height
    barcode_y=round((y+margin+name_height+.5)/DOT)*DOT
    identifier_y=barcode_y+5+.45
    price_y=y+15-margin-price_height
    if identifier_y+id_height>price_y-.2:raise HTTPException(422,'Required compact label fields would overlap; review margins and calibration')
    def cell(value,size,top,height):
        pdf.set_font('Noto',size=size);pdf.set_xy(x+margin,top);pdf.cell(w=usable,h=height,text=value,align='C')
    cell(name,5,y+margin,name_height)
    left=round((x+(config['width_mm']-width)/2)/DOT)*DOT+10*module
    index=0
    while index<len(bits):
        if bits[index]=='0':index+=1;continue
        end=index+1
        while end<len(bits) and bits[end]=='1':end+=1
        pdf.rect(left+index*module,barcode_y,(end-index)*module,5,style='F');index=end
    cell(identifier,4.5,identifier_y,id_height)
    cell(price,5,price_y,price_height)


def validate_rows(rows,config):
    config=validate_config(config)
    pdf=label_pdf(config) if is_compact(config) else None
    for row in rows:
        if pdf is not None:compact_plan(pdf,Label(**{key:row[key] for key in ['barcode','title','variant','sku','price','currency']}),config)
        else:symbol_geometry(row['barcode'],config)


class PDFBackend:
    def render(self,labels,config):
        config=validate_config(config);c=config;w=c['width_mm'];h=c['height_mm'];columns=c['labels_per_row']
        pagewidth,pageheight=(210,297) if c['layout']=='a4' else (w*columns,h)
        rows=max(1,floor((pageheight+c['gap_mm'])/(h+c['gap_mm']))) if c['layout']=='a4' else 1
        pdf=FPDF(unit='mm',format=(pagewidth,pageheight));pdf.set_auto_page_break(False);pdf.set_margins(0,0,0)
        pdf.add_font('Noto',fname=str(FONT_DIR/'NotoSans-Regular.ttf'));pdf.add_font('Sinhala',fname=str(FONT_DIR/'NotoSansSinhala-Regular.ttf'))
        pdf.set_font('Noto',size=c['font_pt']);pdf.set_fallback_fonts(['Sinhala']);pdf.set_text_shaping(True)
        pdf.set_title('PixiePinks Code 128 labels — document generated, not physically printed')
        count=0
        for label in labels:
            slot=count%(columns*rows)
            if slot==0:pdf.add_page()
            x=(slot%columns)*w+c['offset_x_mm'];y=(slot//columns)*(h+c['gap_mm'])+c['offset_y_mm']
            if is_compact(c):
                draw_compact(pdf,label,c,x,y)
                count+=1
                if count>settings.RETAIL_MAX_LABELS:raise HTTPException(422,'Print job exceeds configured label limit')
                continue
            bits,module,symbolwidth=symbol_geometry(label.barcode,c)
            lines=[]
            if c['show_brand']:lines.append('PIXIEPINKS')
            name=label.title if c['show_variant'] else label.title+(' · '+label.variant if label.variant and label.variant!='Default Title' else '')
            lines.append(name)
            if c['show_variant'] and label.variant and label.variant!='Default Title':lines.append(label.variant)
            below=[label.barcode]
            if c['show_sku'] and label.sku:below.append('SKU '+label.sku)
            if c['show_price'] and label.price:below.append(('Rs. ' if label.currency=='LKR' else label.currency+' ')+label.price)
            lineheight=c['font_pt']*.352778*1.2
            required=(len(lines)+len(below))*lineheight+c['barcode_height_mm']+2*c['margin_mm']
            if required>h:raise HTTPException(422,'Text and barcode do not fit the label height. Hide optional fields or increase label height.')
            def text_line(text,baseline,exact=False):
                value=text if exact else text[:c['max_name_chars']]
                while value and pdf.get_string_width(value)>w-2*c['margin_mm']:
                    if exact:raise HTTPException(422,'Human-readable barcode does not fit. Choose a larger label or smaller text.')
                    value=value[:-1]
                pdf.set_xy(x+c['margin_mm'],baseline-lineheight+.35)
                pdf.cell(w=w-2*c['margin_mm'],h=lineheight,text=value,align='C')
            top=y+c['margin_mm']
            for line in lines:top+=lineheight;text_line(line,top-.4,exact=line=='PIXIEPINKS')
            # Every bar edge is on the physical 203-dpi dot grid; retain 10-module quiet zones.
            left=round((x+(w-symbolwidth)/2)/DOT)*DOT+10*module
            barcode_y=round(top/DOT)*DOT
            index=0
            while index<len(bits):
                if bits[index]=='0':index+=1;continue
                end=index+1
                while end<len(bits) and bits[end]=='1':end+=1
                pdf.rect(left+index*module,barcode_y,(end-index)*module,c['barcode_height_mm'],style='F');index=end
            top=barcode_y+c['barcode_height_mm']
            for i,line in enumerate(below):top+=lineheight;text_line(line,top-.3,exact=True)
            count+=1
            if count>settings.RETAIL_MAX_LABELS:raise HTTPException(422,'Print job exceeds configured label limit')
        if not count:raise HTTPException(422,'Select at least one label')
        raw=bytes(pdf.output())
        if len(raw)>MAX_PDF:raise HTTPException(422,'Generated PDF exceeds safety limit; split the batch')
        return raw

backend:LabelBackend=PDFBackend()


def print_rows(db,user,selection_token,default_copies=1,copies=None,require_lkr=False):
    selected=service.preview_record(db,user,selection_token,'SELECTION');copies=copies or {}
    if type(default_copies) is not int or not 1<=default_copies<=1000 or not isinstance(copies,dict) or len(copies)>10000:raise HTTPException(422,'Copies must be 1–1000 per variant')
    if set(copies)-{str(i) for i in selected.payload['ids']}:raise HTTPException(422,'Copy overrides include unselected variants')
    ctx=service.audit_context(db);result=[];total=0
    for row in db.query(RetailVariant).filter(RetailVariant.shop==service.shop(),RetailVariant.id.in_(selected.payload['ids'])).order_by(RetailVariant.id).yield_per(250):
        value=service.scan_value(row);flags=service.issues(row,ctx)
        if row.missing or row.product_status!='ACTIVE' or not service.code_valid(value) or 'SCAN_CONFLICT' in flags or 'BARCODE_RECONCILIATION' in flags:raise HTTPException(409,'Selection contains inactive, missing, invalid or conflicting scan identifiers. Review the Product Audit.')
        if require_lkr and row.currency!='LKR':raise HTTPException(422,'Compact label requires a confirmed Shopify variant price currency of LKR')
        count=copies.get(str(row.id),default_copies)
        if type(count) is not int or not 1<=count<=1000:raise HTTPException(422,'Copies must be 1–1000 per variant')
        total+=count
        if total>settings.RETAIL_MAX_LABELS:raise HTTPException(422,'Too many labels; split the print job')
        result.append({'id':row.id,'copies':count,'barcode':value,'title':row.title,'variant':row.variant_title,
            'sku':service.sku_value(row),'price':str(row.price) if row.price is not None else '', 'currency':row.currency or 'LKR'})
    return result,total


def labels(rows,limit=None):
    count=0
    for row in rows:
        item=Label(**{k:row[k] for k in ['barcode','title','variant','sku','price','currency']})
        for _ in range(row['copies']):
            if limit is not None and count>=limit:return
            yield item;count+=1


def create_job(db,user,rows,total,config,parent=None):
    config=validate_config(config)
    # Verify all exact identifiers and layout before queueing a large document.
    validate_rows(rows,config)
    backend.render(labels(rows,limit=1),config)
    job=RetailPrintJob(shop=service.shop(),actor_id=user.id,parent_id=parent,rows=rows,total=total,template=config,status='QUEUED')
    db.add(job);db.flush();service.audit(db,user,'PRINT_QUEUED',job_id=job.id,total=total,reprint_of=parent);return job


def path(reference):
    if not isinstance(reference,str) or not __import__('re').fullmatch('[a-f0-9]{32}',reference):raise HTTPException(404,'Print document unavailable')
    return Path(settings.RETAIL_LABEL_DIR).resolve()/(reference+'.pdf')


def read_pdf(job):
    try:
        location=path(job.file_ref)
        if location.is_symlink():raise OSError()
        with location.open('rb') as file:raw=file.read(MAX_PDF+1)
        if len(raw)>MAX_PDF or hashlib.sha256(raw).hexdigest()!=job.sha256:raise OSError()
        return raw
    except OSError:raise HTTPException(409,'Print file missing or damaged. Create a reprint from the retained snapshot.') from None


def dispatch_one(sessions):
    with sessions() as db:
        service.write_lock(db)
        for abandoned in db.query(RetailPrintJob).filter(RetailPrintJob.shop==service.shop(),RetailPrintJob.status=='GENERATING',RetailPrintJob.claimed_at<datetime.utcnow()-timedelta(minutes=15)).all():
            abandoned.status='FAILED';abandoned.error='PDF generation interrupted. Reprint from the retained snapshot.'
        job=db.execute(select(RetailPrintJob).where(RetailPrintJob.shop==service.shop(),RetailPrintJob.status=='QUEUED').order_by(RetailPrintJob.id).limit(1).with_for_update(skip_locked=True)).scalar_one_or_none()
        if not job:db.commit();return False
        job.status='GENERATING';job.claimed_at=datetime.utcnow();rid=job.id;rows=job.rows;config=job.template;db.commit()
    try:
        raw=backend.render(labels(rows),config);reference=secrets.token_hex(16);location=path(reference)
        location.parent.mkdir(parents=True,exist_ok=True,mode=0o700);temporary=location.with_suffix('.tmp')
        try:
            with temporary.open('xb') as file:
                os.chmod(temporary,0o600);file.write(raw);file.flush();os.fsync(file.fileno())
            os.replace(temporary,location)
        finally:temporary.unlink(missing_ok=True)
        with sessions() as db:
            job=db.get(RetailPrintJob,rid);job.file_ref=reference;job.sha256=hashlib.sha256(raw).hexdigest();job.status='GENERATED';job.generated_at=datetime.utcnow();db.commit()
    except Exception:
        with sessions() as db:
            job=db.get(RetailPrintJob,rid);job.status='FAILED';job.error='PDF generation failed. Review label size and available storage; reprint does not send to hardware.';db.commit()
    return True


def ensure_reprint_safe(db,job):
    ctx=service.audit_context(db)
    current_rows={row.id:row for row in db.query(RetailVariant).filter(RetailVariant.shop==service.shop(),RetailVariant.id.in_([item['id'] for item in job.rows])).yield_per(250)}
    for row in job.rows:
        current=current_rows.get(row['id'])
        if current is None or current.missing or current.product_status!='ACTIVE' or service.scan_value(current)!=row['barcode'] or any(flag in service.issues(current,ctx) for flag in ['SCAN_CONFLICT','BARCODE_RECONCILIATION']):
            raise HTTPException(409,'Retained barcode is no longer safe to reprint. Review current variants and generate a new batch.')
