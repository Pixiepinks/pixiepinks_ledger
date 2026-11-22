"""Supplier subledger and approved receiving; no second stock balance or Shopify writer."""
from datetime import date,datetime,timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select
from models import Supplier,User
from purchasing_models import PurchaseRecord as Record,PurchaseMovement as Movement,PurchaseAudit as Audit
from inventory_models import InventoryDocument,InventoryEvent
from settings import settings
import inventory_service as inventory
import inventory_shopify as remote
import retail_service as retail
from pos_money import money,fmt,discount,allocate
from retail_inventory import InventoryError

PERMISSIONS=('retail.purchases.view','retail.purchases.prepare','retail.purchases.pay','retail.purchases.approve')

def require(user,permission=PERMISSIONS[0],admin=False):
    if not settings.PURCHASING_ENABLED:raise HTTPException(404,'Purchasing is disabled')
    inventory.require(user,permission,admin=admin)

def audit(db,user,action,record=None,**detail):
    db.add(Audit(shop=retail.shop(),actor_id=user.id,record_id=record.id if record else None,action=action,detail=detail))

def get(db,rid,kind=None):
    row=db.execute(select(Record).where(Record.id==rid,Record.shop==retail.shop()).with_for_update()).scalar_one_or_none()
    if not row or kind and row.kind!=kind:raise HTTPException(404,'Purchase record not found')
    return row

def supplier_profile(db,sid):
    row=db.query(Record).filter_by(shop=retail.shop(),kind='SUPPLIER',business_key='supplier:'+str(sid)).first()
    return {**row.payload,'version':row.version,'profile_id':row.id} if row else {'archived':False,'version':None}

def location(db,gid,verify_remote=True):
    row=db.query(Record).filter_by(shop=retail.shop(),kind='LOCATION',business_key='location:'+str(gid)).first()
    if not row or not row.payload['enabled']:raise HTTPException(409,'Configure an enabled shop/warehouse location first')
    if verify_remote:
        active={r['id'] for r in remote.client.locations() if r.get('isActive') is True}
        if gid not in active:raise HTTPException(409,'Destination is no longer an active verified Shopify location')
    return row

def put_directory(db,user,kind,data):
    require(user,PERMISSIONS[3],admin=True);retail.write_lock(db)
    if kind=='SUPPLIER':
        sid=data.get('id');supplier=db.get(Supplier,sid) if sid else None
        name=inventory.text(data.get('name'),200,True)
        duplicate=db.query(Supplier).filter(Supplier.name.ilike(name)).first()
        if duplicate and (not supplier or duplicate.id!=supplier.id):raise HTTPException(409,'Supplier already exists; reuse its ID')
        if sid and not supplier:raise HTTPException(404,'Supplier not found')
        if not supplier:supplier=Supplier(name=name);db.add(supplier);db.flush()
        supplier.name=name;supplier.email=inventory.text(data.get('email',''),200);supplier.phone=inventory.text(data.get('phone',''),100)
        payload={k:inventory.text(data.get(k,''),1000) for k in ('contact','address')}
        payload.update(credit_days=inventory.amount(data.get('credit_days',0),0,3650),archived=data.get('archived') is True)
        key='supplier:'+str(supplier.id)
    else:
        gid=data.get('location_id');active={r['id']:r for r in remote.client.locations() if r.get('isActive') is True}
        if gid not in active or data.get('type') not in {'SHOP','WAREHOUSE'}:raise HTTPException(422,'Select an actual active Shopify location and SHOP/WAREHOUSE type')
        payload={'location_id':gid,'name':active[gid]['name'],'type':data['type'],'enabled':data.get('enabled') is True};key='location:'+gid;supplier=None
    row=db.query(Record).filter_by(shop=retail.shop(),business_key=key).one_or_none()
    if row:
        if data.get('version')!=row.version:raise HTTPException(409,'Directory changed; reload before saving')
        before=row.payload;row.payload=payload;row.version+=1
    else:
        before=None;row=Record(id=str(uuid4()),shop=retail.shop(),kind=kind,supplier_id=supplier.id if supplier else None,client_key=uuid4().hex,business_key=key,request_hash=inventory.digest(payload),payload=payload,status='ACTIVE',actor_id=user.id);db.add(row)
    audit(db,user,'DIRECTORY_CHANGED',row,before=before,after=payload);db.commit();return row

def quote(db,data,verify_remote=True):
    sid=inventory.amount(data.get('supplier_id'),1);supplier=db.get(Supplier,sid)
    if not supplier or supplier_profile(db,sid).get('archived'):raise HTTPException(422,'Choose an active existing supplier')
    loc=data.get('location_id');location(db,loc,verify_remote=verify_remote)
    try:day=date.fromisoformat(data.get('date',''))
    except (ValueError,TypeError):raise HTTPException(422,'Invoice date required') from None
    if day>datetime.now(ZoneInfo('Asia/Colombo')).date():raise HTTPException(422,'Future invoice date not permitted')
    raw=data.get('lines')
    if not isinstance(raw,list) or not 1<=len(raw)<=20:raise HTTPException(422,'Supply 1–20 catalogue variants')
    lines=[];seen=set()
    for item in raw:
        if not isinstance(item,dict):raise HTTPException(422,'Product line must be an object')
        ident=inventory.identity(db,item.get('variant_id'),item.get('version'),loc)
        if ident['variant_id'] in seen:raise HTTPException(422,'Combine duplicate variant lines')
        seen.add(ident['variant_id']);qty=inventory.amount(item.get('quantity'),1)
        cost=money(item.get('unit_cost'));gross=cost*qty;cut=discount(gross,item.get('discount',{'type':'FIXED','value':'0'}))
        rate=money(item.get('tax_rate','0'))
        if rate>100:raise HTTPException(422,'Tax percentage exceeds 100')
        variant=db.get(retail.RetailVariant,ident['local_id'])
        lines.append({**ident,'image_url':variant.image,'quantity':qty,'unit_cost':fmt(cost),'gross':fmt(gross),'line_discount':fmt(cut),'tax_rate':fmt(rate)})
    nets=[Decimal(l['gross'])-Decimal(l['line_discount']) for l in lines]
    header=discount(sum(nets),data.get('discount',{'type':'FIXED','value':'0'}));shares=allocate(header,nets)
    mode=data.get('tax_mode','EXCLUSIVE')
    if mode not in {'EXCLUSIVE','INCLUSIVE','NONE'}:raise HTTPException(422,'Invalid tax mode')
    total=Decimal(0);taxes=Decimal(0)
    for i,line in enumerate(lines):
        net=nets[i]-Decimal(shares[i])/100;rate=Decimal(line['tax_rate'])
        tax=Decimal(fmt(net*rate/100 if mode=='EXCLUSIVE' else net-net/(1+rate/100) if mode=='INCLUSIVE' else Decimal(0)))
        line.update(header_discount=fmt(Decimal(shares[i])/100),tax=fmt(tax),total=fmt(net+tax if mode=='EXCLUSIVE' else net))
        taxes+=tax;total+=Decimal(line['total'])
    charges=money(data.get('charges','0'));charge_tax=money(data.get('charge_tax','0'));total+=charges+charge_tax
    money(total,Decimal('999999999999.99'))
    return {'supplier_id':sid,'supplier_name':supplier.name,'invoice':inventory.text(data.get('invoice'),120,True),'date':day.isoformat(),'location_id':loc,'due_date':(day+timedelta(days=supplier_profile(db,sid).get('credit_days',0))).isoformat(),'currency':'LKR','tax_mode':mode,'lines':lines,'quantity_total':sum(l['quantity'] for l in lines),'subtotal':fmt(sum(Decimal(l['gross']) for l in lines)),'line_discount_total':fmt(sum(Decimal(l['line_discount']) for l in lines)),'invoice_discount':fmt(header),'taxable_amount':fmt(sum(nets)-header-taxes if mode=='INCLUSIVE' else sum(nets)-header),'discount_total':fmt(header+sum(Decimal(l['line_discount']) for l in lines)),'line_tax_total':fmt(taxes),'tax_total':fmt(taxes+charge_tax),'charges':fmt(charges),'charge_tax':fmt(charge_tax),'total':fmt(total),'notes':inventory.text(data.get('notes',''),2000)}

def create(db,user,kind,data):
    require(user,PERMISSIONS[2] if kind=='PAYMENT' else PERMISSIONS[1]);retail.write_lock(db)
    key=inventory.text(data.get('client_key'),64,True)
    if len(key)<16:raise HTTPException(422,'Stable request key must contain at least 16 characters')
    hashed=inventory.digest(data);old=db.query(Record).filter_by(shop=retail.shop(),client_key=key).first()
    if old:
        if old.request_hash!=hashed:raise HTTPException(409,'Request key reused with different data')
        return old
    parent=None;business=None;sid=None
    if kind=='BILL':
        payload=quote(db,data);sid=payload['supplier_id'];business='invoice:'+inventory.digest([sid,payload['invoice'].casefold()])
        if db.query(Record).filter_by(shop=retail.shop(),business_key=business).first():raise HTTPException(409,'Supplier invoice already recorded')
    elif kind in {'RECEIPT','PAYMENT'}:
        parent=get(db,data.get('bill_id'),'BILL');sid=parent.supplier_id
        if parent.status!='APPROVED':raise HTTPException(409,'Approved purchase bill required')
        if kind=='PAYMENT':
            amount=money(data.get('amount'))
            if data.get('verified') is not True or amount<=0 or data.get('method') not in {'CASH','BANK','CARD'}:raise HTTPException(422,'Confirm an actual verified supplier payment')
            paid=sum((Decimal(r.payload['amount']) for r in db.query(Record).filter_by(parent_id=parent.id,kind='PAYMENT')),Decimal(0))
            if paid+amount>Decimal(parent.payload['total']):raise HTTPException(409,'Payment exceeds remaining balance')
            payment_date=date.fromisoformat(data['date'])
            if payment_date>datetime.now(ZoneInfo('Asia/Colombo')).date():raise HTTPException(422,'Payment date cannot be in the future')
            payment_reference=inventory.text(data.get('reference'),200,True)
            business='payment:'+inventory.digest([parent.id,data['method'],payment_reference.casefold()])
            if db.query(Record).filter_by(shop=retail.shop(),business_key=business).first():raise HTTPException(409,'Supplier payment reference already recorded; review its payment history')
            payload={'amount':fmt(amount),'method':data['method'],'reference':payment_reference,'date':payment_date.isoformat(),'verified':True}
        else:
            loc=data.get('location_id');location(db,loc);raw=data.get('lines')
            if not isinstance(raw,list) or not 1<=len(raw)<=20:raise HTTPException(422,'Receipt lines required')
            normalized=[];seen=set()
            for l in raw:
                if not isinstance(l,dict):raise HTTPException(422,'Receipt line must be an object')
                index=l.get('bill_line')
                if type(index) is not int or not 0<=index<len(parent.payload['lines']) or index in seen:raise HTTPException(422,'Invalid/duplicate bill line')
                seen.add(index);values={k:inventory.amount(l.get(k,0)) for k in ('accepted','damaged','rejected')}
                if not sum(values.values()):raise HTTPException(422,'Record at least one received, damaged or rejected unit')
                normalized.append({'bill_line':index,**values})
            delivery_reference=inventory.text(data.get('delivery_reference'),120,True)
            business='delivery:'+inventory.digest([parent.id,delivery_reference.casefold()])
            if db.query(Record).filter_by(shop=retail.shop(),business_key=business).first():raise HTTPException(409,'This bill delivery reference already exists; review its receipt instead of recording it twice')
            received_date=date.fromisoformat(data.get('date',datetime.now(ZoneInfo('Asia/Colombo')).date().isoformat()))
            if received_date>datetime.now(ZoneInfo('Asia/Colombo')).date():raise HTTPException(422,'Receiving date cannot be in the future')
            payload={'date':received_date.isoformat(),'delivery_reference':delivery_reference,'location_id':loc,'lines':normalized,'notes':inventory.text(data.get('notes',''),2000)}
    else:raise HTTPException(422,'Unsupported purchasing record')
    row=Record(id=str(uuid4()),shop=retail.shop(),kind=kind,parent_id=parent.id if parent else None,supplier_id=sid,client_key=key,business_key=business,request_hash=hashed,payload=payload,status='RECORDED' if kind=='PAYMENT' else 'DRAFT',actor_id=user.id)
    db.add(row);db.flush();audit(db,user,kind+'_CREATED',row,payload_hash=inventory.digest(payload));db.commit();return row

def action(db,user,rid,action,confirmation=None):
    require(user,PERMISSIONS[1] if action=='submit' else PERMISSIONS[3],admin=action!='submit');retail.write_lock(db);row=get(db,rid)
    if confirmation!=row.id:raise HTTPException(422,'Confirm exact record ID')
    if action=='submit' and row.status=='DRAFT':row.status='REVIEW'
    elif action=='void' and row.status in {'DRAFT','REVIEW','APPROVED'}:
        if db.query(Record).filter_by(parent_id=row.id).count() or db.query(Movement).filter_by(receipt_id=row.id).count():raise HTTPException(409,'Dependent receipts/payments prevent voiding; do not reverse stock implicitly')
        row.status='VOID'
    elif action=='approve' and row.status=='REVIEW' and row.kind in {'BILL','RECEIPT'}:
        if row.kind=='RECEIPT':
            bill=get(db,row.parent_id,'BILL')
            if bill.status!='APPROVED':raise HTTPException(409,'Bill no longer approved')
            location(db,row.payload['location_id'])
            for line in row.payload['lines']:
                index=line['bill_line'];source=bill.payload['lines'][index]
                prior=db.query(Movement).join(Record,Movement.receipt_id==Record.id).filter(Record.parent_id==bill.id,Movement.bill_line==index).all()
                used=sum(m.accepted+m.damaged+m.rejected for m in prior)
                if used+sum(line[k] for k in ('accepted','damaged','rejected'))>source['quantity']:raise HTTPException(409,'Receipt exceeds remaining ordered quantity')
                document=None
                if line['accepted']:
                    current=db.get(retail.RetailVariant,source['local_id'])
                    if current is None:raise HTTPException(409,'Catalogue variant no longer available')
                    ident=inventory.identity(db,source['local_id'],current.version,row.payload['location_id'])
                    if any(ident[k]!=source[k] for k in ('variant_id','product_id','inventory_item_id')):raise HTTPException(409,'Catalogue identity changed')
                    payload={'prepared_by':user.username,'supplier_id':bill.supplier_id,'supplier_name':bill.payload['supplier_name'],'invoice':bill.payload['invoice'],'date':row.payload['date'],'purchase_receipt_id':row.id,'purchase_bill_id':bill.id,'notes':'Approved goods receipt','lines':[{**ident,'quantity':line['accepted'],'unit_cost':source['unit_cost'],'cost_currency':'LKR'}]}
                    document=InventoryDocument(id=str(uuid4()),shop=row.shop,client_key='purchase-'+uuid4().hex,kind='RECEIPT',reference='GRN-'+uuid4().hex[:16].upper(),business_key=inventory.digest([row.id,index]),payload=payload,payload_hash=inventory.digest(payload),contract_version=remote.CONTRACT_VERSION,actor_id=user.id,dispatch_key=str(uuid4()),status='DRAFT');db.add(document);db.flush()
                    inventory.event(db,document,'PURCHASE_RECEIPT_POSTED',user,receipt_id=row.id,bill_line=index,accepted=line['accepted'])
                db.add(Movement(id=str(uuid4()),receipt_id=row.id,bill_line=index,accepted=line['accepted'],damaged=line['damaged'],rejected=line['rejected'],document_id=document.id if document else None))
        row.status='APPROVED';row.approved_by=user.id
    elif action=='approve' and row.status=='APPROVED':return row
    else:raise HTTPException(409,'Invalid purchasing state transition')
    row.version+=1;audit(db,user,row.kind+'_'+action.upper(),row);db.commit();return row

def public(db,row):
    result={k:getattr(row,k) for k in ('id','kind','parent_id','supplier_id','payload','status','version','actor_id','approved_by')}
    if row.kind=='BILL':
        # Display additions for existing frozen invoices; never update their JSON or totals.
        p=row.payload;lines=p['lines']
        result['payload']={**p,'quantity_total':sum(l['quantity'] for l in lines),'subtotal':fmt(sum((Decimal(l['gross']) for l in lines),Decimal(0))),'line_discount_total':fmt(sum((Decimal(l['line_discount']) for l in lines),Decimal(0))),'invoice_discount':fmt(sum((Decimal(l['header_discount']) for l in lines),Decimal(0))),'line_tax_total':fmt(sum((Decimal(l['tax']) for l in lines),Decimal(0)))}
        paid=sum((Decimal(r.payload['amount']) for r in db.query(Record).filter_by(parent_id=row.id,kind='PAYMENT')),Decimal(0))
        result['payments']=[{'id':p.id,**p.payload,'actor_id':p.actor_id} for p in db.query(Record).filter_by(parent_id=row.id,kind='PAYMENT').order_by(Record.created_at)]
        result['receipts']=[{'id':p.id,'status':p.status,'lines':p.payload['lines']} for p in db.query(Record).filter_by(parent_id=row.id,kind='RECEIPT').order_by(Record.created_at)]
        result.update(paid=fmt(paid),balance=fmt(Decimal(row.payload['total'])-paid),payment_status='PAID' if paid==Decimal(row.payload['total']) else 'PARTIAL' if paid else 'PENDING')
    if row.kind=='RECEIPT':
        result['movements']=[]
        for m in db.query(Movement).filter_by(receipt_id=row.id):
            doc=db.get(InventoryDocument,m.document_id) if m.document_id else None
            state='Not applicable' if not doc else 'Synced' if doc.status=='VERIFIED' else 'Failed' if doc.status=='CONFLICT' and doc.attempts==0 else 'Needs Review' if doc.status in {'UNCERTAIN','APPLIED_REVIEW','RECONCILED_MANUALLY','REJECTED'} else 'Pending'
            result['movements'].append({'bill_line':m.bill_line,'accepted':m.accepted,'damaged':m.damaged,'rejected':m.rejected,'document_id':m.document_id,'sync_status':state,'attempts':doc.attempts if doc else 0,'error':doc.error if doc else None,'remote_reference':doc.remote_reference if doc else None})
    result['audit']=[{'action':a.action,'actor_id':a.actor_id,'at':a.created_at.isoformat()+'Z','detail':a.detail} for a in db.query(Audit).filter_by(shop=row.shop,record_id=row.id).order_by(Audit.id)]
    return result

def prepare_one(sessions,client=None):
    if not (settings.PURCHASING_ENABLED and settings.PURCHASE_INVENTORY_SYNC_ENABLED and settings.SHOPIFY_INVENTORY_WRITE_ENABLED and remote.MUTATION_CONTRACT_VERIFIED):return False
    with sessions() as db:
        retail.write_lock(db)
        doc=db.query(InventoryDocument).join(Movement,Movement.document_id==InventoryDocument.id).filter(InventoryDocument.shop==retail.shop(),InventoryDocument.status=='DRAFT').order_by(InventoryDocument.created_at).first()
        if not doc:return False
        try:
            receipt=get(db,doc.payload['purchase_receipt_id'],'RECEIPT');user=db.get(User,receipt.approved_by)
            require(user,PERMISSIONS[3],admin=True);location(db,doc.payload['lines'][0]['location_id'])
            if receipt.status!='APPROVED':raise InventoryError('PURCHASE_RECEIPT_NOT_APPROVED')
            inventory.submit(db,user,doc.id,client=client,commit=False,purchasing=True)
            inventory.approve(db,user,doc.id,doc.reference,commit=False,purchasing=True)
            db.commit()
        except (InventoryError,HTTPException) as error:
            document_id=doc.id;db.rollback();doc=inventory.get(db,document_id);doc.status='CONFLICT';doc.error=error.reason if isinstance(error,InventoryError) else 'PURCHASE_PREFLIGHT_FAILED';inventory.event(db,doc,'PURCHASE_PREFLIGHT_FAILED',reason=doc.error);db.commit()
        return True

def retry(db,user,document_id):
    require(user,PERMISSIONS[3],admin=True);retail.write_lock(db);doc=inventory.get(db,document_id)
    if not db.query(Movement).filter_by(document_id=doc.id).first() or doc.attempts!=0 or doc.status!='CONFLICT':raise HTTPException(409,'Only undispatched failures may retry; attempted outcomes require reconciliation without resend')
    if doc.plan is not None and doc.approved_by!=user.id:raise HTTPException(403,'Original approving administrator must renew this frozen approval')
    receipt=get(db,doc.payload['purchase_receipt_id'],'RECEIPT');location(db,doc.payload['lines'][0]['location_id'])
    if receipt.status!='APPROVED':raise HTTPException(409,'Approved receipt required')
    doc.status='APPROVED' if doc.plan is not None else 'DRAFT';doc.error=None
    inventory.event(db,doc,'PURCHASE_RETRY_APPROVED' if doc.plan is not None else 'PURCHASE_RETRY_REQUESTED',user,approval_hash=doc.approval_hash,automatic_retry=False);db.commit()

def comparison(db,user,document_id,client=None):
    """Compare observations, never infer an absolute CRM balance or mutation success."""
    require(user);doc=inventory.get(db,document_id)
    if not db.query(Movement).filter_by(document_id=doc.id).first():raise HTTPException(404,'Purchase movement not found')
    observed=inventory.observe_lines(db,doc,client or remote.client)
    events=db.query(InventoryEvent).filter_by(document_id=doc.id).order_by(InventoryEvent.id.desc()).all()
    recorded=next((e.detail['after'] for e in events if e.action in {'READBACK_VERIFIED','READBACK_REQUIRES_REVIEW'} and e.detail.get('after')),doc.baseline)
    return {'document_id':doc.id,'last_recorded_observation':recorded,'shopify_now':observed,'different':None if not recorded else any(a['on_hand']!=b['on_hand'] or a['available']!=b['available'] for a,b in zip(recorded,observed)),'authoritative_crm_balance':False,'automatic_correction':False,'warning':'Later sales, refunds and transfers can change Shopify stock. Quantity equality does not prove receipt application; reconcile the adjustment identity, never resend an attempted mutation.'}


def edit_bill(db,user,rid,data):
    require(user,PERMISSIONS[1]);retail.write_lock(db);row=get(db,rid,'BILL')
    if row.status!='DRAFT' or data.get('version')!=row.version:raise HTTPException(409,'Only the current draft version can be edited')
    payload=quote(db,data)
    if payload['supplier_id']!=row.supplier_id or payload['invoice'].casefold()!=row.payload['invoice'].casefold():raise HTTPException(409,'Supplier and invoice identity are fixed; void incorrect drafts')
    before=inventory.digest(row.payload);row.payload=payload;row.version+=1
    audit(db,user,'BILL_DRAFT_EDITED',row,before_hash=before,after_hash=inventory.digest(payload));db.commit();return row
