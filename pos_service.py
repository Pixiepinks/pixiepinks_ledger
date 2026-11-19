"""Authoritative POS carts, canonical commercial records, drawer and durable remote intent."""
import copy,csv,io,json,re,secrets
from uuid import uuid4
from datetime import datetime,timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select,func
from models import User,Customer,Account,SalesOrder,SalesOrderLine,SalesPayment,JournalEntry,JournalLine
from pos_models import *
from settings import settings
import retail_service as retail
import inventory_service as inventory
import pos_shopify as remote
import pos_money as calc
import sales_payment_service as payments

PERMISSIONS=['view','sell','discount','hold','refund','void','register_open','register_close','reports','admin']
ZERO={'type':'FIXED','value':'0.00'}
DEFAULT={'tax_mode':'UNCONFIGURED','tax_rate':'0.00','tax_title':'','legal_id':'','shop_contact':'','discount_limits':{},
 'pricing_policy':'NET_PRICE_SNAPSHOT','accounts':{}}


def require(user,action='view',supervisor=False):
    if not settings.POS_ENABLED:raise HTTPException(404,'POS disabled')
    if not user or not user.is_active or not (user.role=='ADMIN' or ('pos.view' in (user.section_access or []) and 'pos.'+action in (user.section_access or []))):raise HTTPException(403,'POS permission required: '+action)
    if supervisor and user.role!='ADMIN' and 'pos.admin' not in (user.section_access or []):raise HTTPException(403,'POS supervisor authorization required')


def locked(db):return retail.write_lock(db)
def audit(db,user,ref,action,**detail):db.add(POSAudit(shop=retail.shop(),reference=str(ref),action=action,actor_id=user.id if user else None,detail=detail))
def digest(value):return inventory.digest(value)
def cfg(db):
    row=db.get(POSConfig,retail.shop());return ({**DEFAULT,**row.value},row.version) if row else (copy.deepcopy(DEFAULT),0)

def configure(db,user,value):
    require(user,'admin');locked(db)
    if not isinstance(value,dict) or set(value)!=set(DEFAULT):raise HTTPException(422,'Complete explicit POS policy required')
    if value['tax_mode'] not in {'NONE','EXCLUSIVE'} or value['pricing_policy']!='NET_PRICE_SNAPSHOT':raise HTTPException(422,'Select explicit NONE/EXCLUSIVE tax and net-price snapshot policy')
    if calc.money(value['tax_rate'])>100 or (value['tax_mode']=='NONE' and calc.money(value['tax_rate'])!=0):raise HTTPException(422,'Invalid tax rate')
    for key in ['tax_title','legal_id','shop_contact']:inventory.text(value[key],500)
    if value['tax_mode']=='EXCLUSIVE' and not value['tax_title']:raise HTTPException(422,'Tax description required')
    if not isinstance(value['discount_limits'],dict) or any(not isinstance(k,str) or len(k)>30 or calc.money(v)>100 for k,v in value['discount_limits'].items()):raise HTTPException(422,'Invalid role discount limits')
    required={'sales','discount','tax','cash','card','bank','returns'}
    if not isinstance(value['accounts'],dict) or set(value['accounts'])!=required or any(type(v) is not int or not db.get(Account,v) for v in value['accounts'].values()):raise HTTPException(422,'Choose existing GL accounts for every POS posting role')
    if len({value['accounts'][k] for k in ['cash','card','bank']})!=3:raise HTTPException(422,'Separate cash, card-clearing and bank asset accounts required')
    for key in ['cash','card','bank']:
        if db.get(Account,value['accounts'][key]).type!='ASSET':raise HTTPException(422,'Tender accounts must be asset accounts')
    row=db.get(POSConfig,retail.shop())
    if row is None:row=POSConfig(shop=retail.shop(),value=value,version=1);db.add(row)
    else:row.value=value;row.version+=1
    audit(db,user,retail.shop(),'CONFIGURED',policy=value);db.commit()


def register(db,user,name,location):
    require(user,'admin');locked(db);name=inventory.text(name,80,True)
    if location not in {r['id'] for r in inventory.remote.client.locations() if r['isActive']}:raise HTTPException(422,'Choose an actual active Shopify location')
    r=POSRegister(shop=retail.shop(),name=name,location_id=location);db.add(r);db.flush();audit(db,user,r.id,'REGISTER_CREATED',location=location);db.commit();return r


def open_register(db,user,register_id,opening_float):
    require(user,'register_open');locked(db)
    r=db.query(POSRegister).filter_by(shop=retail.shop(),id=register_id).one_or_none()
    if not r:raise HTTPException(404,'Register not found')
    prior=db.query(POSSession).filter_by(active_register_id=r.id).one_or_none()
    if prior:
        if prior.cashier_id==user.id:return prior
        raise HTTPException(409,'Register already open; close it before handover')
    if db.query(POSSession).filter_by(active_cashier_id=user.id).first():raise HTTPException(409,'Close your current register before opening another')
    s=POSSession(id=str(uuid4()),shop=retail.shop(),register_id=r.id,active_register_id=r.id,cashier_id=user.id,active_cashier_id=user.id,opening_float=calc.money(opening_float),status='OPEN');db.add(s)
    audit(db,user,s.id,'REGISTER_OPENED',opening_float=calc.fmt(s.opening_float),register=r.id);db.commit();return s


def session(db,user,sid):
    s=db.query(POSSession).filter_by(shop=retail.shop(),id=sid).with_for_update().one_or_none()
    if not s or s.status!='OPEN':raise HTTPException(409,'Open register session required')
    if s.cashier_id!=user.id and user.role!='ADMIN' and 'pos.admin' not in (user.section_access or []):raise HTTPException(403,'This register belongs to another cashier')
    return s


def drawer(db,s):
    return Decimal(str(s.opening_float))+sum((Decimal(str(m.amount)) for m in db.query(POSCashMovement).filter_by(session_id=s.id)),Decimal(0))

def cash_move(db,user,sid,amount,kind,key,note,offset_account=None):
    require(user,'admin',True);locked(db);s=session(db,user,sid)
    if kind not in {'PAID_IN','PAID_OUT'}:raise HTTPException(422,'Choose paid-in or paid-out')
    value=calc.money(amount)
    if value<=0 or not isinstance(key,str) or not 16<=len(key)<=64:raise HTTPException(422,'Amount and stable request key required')
    ref='drawer:'+s.id+':'+key;prior=db.query(POSCashMovement).filter_by(reference=ref).one_or_none()
    signed=value if kind=='PAID_IN' else -value
    if prior:
        if prior.amount!=signed or prior.kind!=kind or prior.detail!={'note':note,'offset_account':offset_account}:raise HTTPException(409,'Request key already used')
        return
    policy,_=cfg(db)
    if type(offset_account) is not int or not db.get(Account,offset_account) or offset_account==policy['accounts'].get('cash'):raise HTTPException(422,'Choose a distinct existing offset GL account')
    if kind=='PAID_OUT' and drawer(db,s)<value:raise HTTPException(409,'Paid-out exceeds expected drawer cash')
    note=inventory.text(note,500,True)
    db.add(POSCashMovement(session_id=s.id,amount=signed,kind=kind,reference=ref,actor_id=user.id,detail={'note':note,'offset_account':offset_account}))
    entries=[(policy['accounts']['cash'],value,Decimal(0)),(offset_account,Decimal(0),value)]
    if signed<0:entries=[(a,c,d) for a,d,c in entries]
    post_journal(db,ref,entries);audit(db,user,s.id,kind,amount=calc.fmt(value),note=note);db.commit()


def close_register(db,user,sid,counted,notes):
    require(user,'register_close');locked(db);s=session(db,user,sid)
    pending=db.query(POSCheckout).filter(POSCheckout.session_id==sid,POSCheckout.status.notin_(['COMPLETE','CANCELLED'])).first()
    refunds=db.query(POSRefund).filter(POSRefund.session_id==sid,POSRefund.status!='PAID_OUT').first()
    if pending or refunds:raise HTTPException(409,'Resolve pending/uncertain sales and refund payouts before closing')
    expected=drawer(db,s);counted=calc.money(counted);difference=counted-expected
    if difference:require(user,'admin',True)
    s.closing={'expected':calc.fmt(expected),'counted':calc.fmt(counted),'difference':calc.fmt(difference),'notes':inventory.text(notes,1000,True),'closed_by':user.id}
    s.status='CLOSED';s.active_register_id=None;s.active_cashier_id=None;s.closed_at=datetime.utcnow();audit(db,user,s.id,'REGISTER_CLOSED',**s.closing);db.commit();return s


def get_cart(db,user,cart_id,version=None):
    require(user);c=db.query(POSCart).filter_by(shop=retail.shop(),id=cart_id).with_for_update().one_or_none()
    if not c:raise HTTPException(404,'Cart not found')
    if c.actor_id!=user.id and user.role!='ADMIN' and 'pos.admin' not in (user.section_access or []):raise HTTPException(403,'Cart belongs to another cashier')
    if version is not None and c.version!=version:raise HTTPException(409,'Cart changed; reload before editing')
    return c


def new_cart(db,user,sid):
    require(user,'sell');locked(db);session(db,user,sid)
    c=POSCart(id=str(uuid4()),shop=retail.shop(),session_id=sid,actor_id=user.id,status='DRAFT',payload={'lines':[],'order_discount':ZERO,'customer_id':None,'customer_name':'','phone':'','invoice_details':'','discount_reason':''},quote={});db.add(c);db.flush();reprice(db,c);audit(db,user,c.id,'CART_CREATED');db.commit();return c


def reprice(db,c):
    policy,version=cfg(db);lines=[]
    for raw in c.payload['lines']:
        line=inventory.identity(db,raw['id'],raw['version'],raw['location'])
        row=db.get(retail.RetailVariant,raw['id'])
        if row.currency!='LKR' or row.price is None:raise HTTPException(409,'LKR selling price required')
        lines.append({**line,'id':row.id,'version':row.version,'unit_price':calc.fmt(Decimal(str(row.price))),
            'image':row.image,'availability':raw.get('observed'),'quantity':raw['quantity'],'discount':raw['discount'],'taxable':raw['taxable']})
    quote=calc.calculate(lines,c.payload['order_discount'],policy['tax_mode'],calc.money(policy['tax_rate']))
    quote.update(config_version=version,tax_mode=policy['tax_mode'],tax_rate=policy['tax_rate'],tax_title=policy['tax_title'])
    quote['hash']=digest(quote);c.quote=quote;return quote


def edit_cart(db,user,cart_id,version,action,payload,client=None):
    require(user,'sell');locked(db);c=get_cart(db,user,cart_id,version)
    if action=='resume' and c.status=='HELD':
        active=db.query(POSSession).filter_by(shop=retail.shop(),active_cashier_id=user.id).one_or_none()
        if not active:raise HTTPException(409,'Open a register before resuming a held sale')
        location=db.get(POSRegister,active.register_id).location_id
        if any(l['location']!=location for l in c.payload['lines']):raise HTTPException(409,'Resume held sales only at the original Shopify location')
        c.session_id=active.id
    session(db,user,c.session_id)
    if c.status not in {'DRAFT','HELD'}:raise HTTPException(409,'Frozen checkout cannot be edited')
    p=copy.deepcopy(c.payload);r=db.get(POSRegister,db.get(POSSession,c.session_id).register_id)
    if action=='add':
        if set(payload)!={'id','version'}:raise HTTPException(422,'Choose an exact product version')
        line=inventory.identity(db,payload['id'],payload['version'],r.location_id);observed=(client or remote.client).inspect(line)
        product=db.get(retail.RetailVariant,payload['id'])
        if calc.money(observed['price'])!=product.price:raise HTTPException(409,'Shopify price changed; refresh the catalog')
        existing=next((x for x in p['lines'] if x['id']==payload['id']),None)
        if existing:existing['quantity']+=1;existing['observed']=observed
        else:
            if len(p['lines'])>=20:raise HTTPException(422,'Maximum 20 variants per sale')
            p['lines'].append({'id':product.id,'version':product.version,'location':r.location_id,'quantity':1,'discount':ZERO,'taxable':observed['taxable'],'observed':observed})
    elif action=='line':
        if set(payload)!={'id','quantity','discount'}:raise HTTPException(422,'Only quantity and discount can be edited')
        row=next((x for x in p['lines'] if x['id']==payload['id']),None)
        if not row:raise HTTPException(404,'Cart line not found')
        if type(payload['quantity']) is not int or not 0<=payload['quantity']<=99:raise HTTPException(422,'Quantity must be 0–99')
        if payload['quantity']==0:p['lines'].remove(row)
        else:row.update(quantity=payload['quantity'],discount=payload['discount'])
    elif action=='details':
        if set(payload)!={'customer_id','customer_name','phone','invoice_details','order_discount','discount_reason'}:raise HTTPException(422,'Invalid sale detail fields')
        customer=payload['customer_id']
        if customer is not None and (type(customer) is not int or not db.get(Customer,customer)):raise HTTPException(422,'Customer not found')
        for k in ['customer_name','phone','invoice_details','discount_reason']:inventory.text(payload[k],1000 if k=='invoice_details' else 255)
        if payload['phone']:
            from shopify_draft_order_service import normalize_sri_lankan_mobile
            try:payload['phone']=normalize_sri_lankan_mobile(payload['phone'])
            except ValueError:raise HTTPException(422,'Enter a valid Sri Lankan mobile number') from None
        p.update(payload)
    elif action in {'hold','resume','cancel'}:
        require(user,'hold' if action!='cancel' else 'void')
        if payload!={}:raise HTTPException(422,'No extra action fields accepted')
        c.status={'hold':'HELD','resume':'DRAFT','cancel':'CANCELLED'}[action]
    else:raise HTTPException(422,'Unknown cart action')
    p.pop('discount_approval',None);c.payload=p;c.version+=1;c.updated_at=datetime.utcnow();reprice(db,c)
    audit(db,user,c.id,'CART_'+action.upper(),version=c.version);db.commit();return c


def authorize_discount(db,user,cart_id,version,reason):
    require(user,'admin',True);locked(db);c=get_cart(db,user,cart_id,version)
    if c.status not in {'DRAFT','HELD'}:raise HTTPException(409,'Cart is frozen')
    reprice(db,c);p=copy.deepcopy(c.payload);p['discount_approval']={'hash':c.quote['hash'],'authorizer':user.id,'reason':inventory.text(reason,500,True)}
    c.payload=p;c.version+=1;audit(db,user,c.id,'DISCOUNT_AUTHORIZED',**p['discount_approval']);db.commit();return c


def create_customer(db,user,name,phone,consent):
    require(user,'sell');locked(db)
    if consent is not True:raise HTTPException(422,'Explicit operational contact consent required; marketing is separate')
    name=inventory.text(name,255,True)
    from shopify_draft_order_service import normalize_sri_lankan_mobile
    try:phone=normalize_sri_lankan_mobile(phone)
    except ValueError:raise HTTPException(422,'Valid mobile number required') from None
    if db.query(Customer).filter((func.lower(Customer.name)==name.lower())|(func.replace(func.replace(func.replace(Customer.phone,' ',''),'-',''),'+','').in_([phone.lstrip('+'),'0'+phone[3:]]))).first():raise HTTPException(409,'Customer already exists; select their profile')
    row=Customer(name=name,phone=phone);db.add(row);db.flush();audit(db,user,row.id,'CUSTOMER_CONSENT_RECORDED',purpose='POS contact only',marketing=False);db.commit();return row


def tenders(plan,total):
    if not isinstance(plan,list) or not 1<=len(plan)<=5:raise HTTPException(422,'Enter 1–5 tender records')
    output=[];applied=Decimal(0)
    for raw in plan:
        if not isinstance(raw,dict) or set(raw)!={'method','amount','received','reference','confirmed'} or raw['method'] not in {'CASH','CARD_PAYMENT','BANK_TRANSFER'}:raise HTTPException(422,'Use cash, external card or bank transfer without card data')
        amount=calc.money(raw['amount']);received=calc.money(raw['received']);ref=inventory.text(raw['reference'],80)
        if amount<=0:raise HTTPException(422,'Tender amount must be positive')
        if raw['method']!='CASH' and (not ref or received!=amount):raise HTTPException(422,'Non-cash reference and exact amount required')
        if raw['method']=='CARD_PAYMENT' and 13<=len(re.sub(r'\D','',ref))<=19:raise HTTPException(422,'Never enter a full card number')
        if raw['method']=='CASH' and received<amount:raise HTTPException(422,'Cash received must cover the cash amount applied')
        if type(raw['confirmed']) is not bool:raise HTTPException(422,'Explicit tender confirmation required')
        if raw['method'] in {'CASH','CARD_PAYMENT'} and not raw['confirmed']:raise HTTPException(422,'Confirm physical cash/external terminal receipt')
        if raw['method']=='BANK_TRANSFER' and raw['confirmed']:raise HTTPException(422,'Bank transfers require separate supervisor verification')
        applied+=amount;output.append({**raw,'amount':calc.fmt(amount),'received':calc.fmt(received),'change':calc.fmt(received-amount)})
    if applied!=total:raise HTTPException(422,'Applied tenders must exactly equal the sale total')
    return output


def checkout(db,user,cart_id,version,key,plan,client=None):
    require(user,'sell')
    if not settings.POS_CHECKOUT_WRITE_ENABLED:raise HTTPException(409,'POS checkout disabled; hold the cart without accepting tender')
    locked(db);c=get_cart(db,user,cart_id)
    old=db.query(POSCheckout).filter_by(cart_id=c.id).one_or_none()
    if old:
        if old.request_key!=key:raise HTTPException(409,'Checkout already exists; open its recovery status')
        if tenders(plan,Decimal(old.frozen['quote']['grand_total']))!=old.frozen['tenders']:raise HTTPException(409,'Checkout key belongs to different tenders')
        return old
    if c.version!=version:raise HTTPException(409,'Cart changed; reload before checkout')
    if c.status!='DRAFT' or not c.payload['lines']:raise HTTPException(409,'Nonempty active cart required')
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}',key):raise HTTPException(422,'Stable checkout request ID required')
    s=session(db,user,c.session_id);policy,config_version=cfg(db)
    if policy['tax_mode']=='UNCONFIGURED' or not policy['accounts']:raise HTTPException(409,'Administrator must configure explicit tax and accounting policy')
    previous=c.quote['hash'];quote=reprice(db,c)
    if quote['hash']!=previous:
        c.version+=1;audit(db,user,c.id,'QUOTE_REVALIDATED',hash=quote['hash']);db.commit()
        raise HTTPException(409,'Price/policy changed; reload the cart and reconfirm tenders')
    if Decimal(quote['grand_total'])<=0:raise HTTPException(422,'Zero-value checkout requires a separate documented policy')
    if Decimal(quote['discount_total'])>0 and not c.payload.get('discount_reason') and not (c.payload.get('discount_approval') or {}).get('reason'):raise HTTPException(422,'Record a discount reason and apply it before checkout')
    limit=Decimal(str(policy['discount_limits'].get(user.role,0))) if user.role!='ADMIN' else Decimal(100)
    needs=Decimal(quote['discount_total'])>Decimal(quote['subtotal'])*limit/100 or (Decimal(quote['discount_total'])>0 and user.role!='ADMIN' and 'pos.discount' not in (user.section_access or []))
    if needs:
        approval=c.payload.get('discount_approval',{});authorizer=db.get(User,approval.get('authorizer')) if approval else None
        if approval.get('hash')!=quote['hash'] or not authorizer:raise HTTPException(403,'Supervisor discount authorization required')
        require(authorizer,'admin',True)
    api=client or remote.client
    for line in quote['lines']:
        observed=api.inspect(line)
        if calc.money(observed['price'])!=calc.money(line['unit_price']) or observed['taxable']!=line['taxable']:raise HTTPException(409,'Product price/tax state changed; refresh cart')
        if type(observed.get('available')) is not int or observed['available']<line['quantity']:raise HTTPException(409,'Verified available stock is insufficient; no stock override is enabled')
    tender_plan=tenders(plan,Decimal(quote['grand_total']))
    customer=db.get(Customer,c.payload['customer_id']) if c.payload.get('customer_id') else None
    reference='POS-'+secrets.token_hex(10).upper()
    order=SalesOrder(sales_channel='POS',internal_order_number=reference,idempotency_key='pos:'+c.id,customer_id=customer.id if customer else None,
        customer_name=customer.name if customer else c.payload['customer_name'] or 'Walk-in Customer',whatsapp_phone=customer.phone or '' if customer else c.payload['phone'],
        customer_notes=c.payload['invoice_details'],order_status='PAYMENT_VERIFICATION',payment_status='UNPAID',fulfillment_status='UNFULFILLED',
        currency='LKR',created_by=user.username,subtotal=quote['subtotal'],discount_total=quote['discount_total'],tax_total=quote['tax_total'],grand_total=quote['grand_total'],shipping_total=0)
    db.add(order);db.flush()
    for line in quote['lines']:db.add(SalesOrderLine(sales_order_id=order.id,shopify_product_id=line['product_id'],shopify_variant_id=line['variant_id'],sku=line['sku'],product_name_snapshot=line['title'],variant_name_snapshot=line['variant_title'],product_image_url=line['image'],quantity=line['quantity'],unit_price=line['unit_price'],discount_amount=str(Decimal(line['line_discount'])+Decimal(line['order_discount'])),tax_amount=line['tax'],line_total=line['total']))
    db.flush()  # Canonical lines exist before the immutable checkout boundary.
    binding=db.query(POSCustomerBinding).filter_by(shop=retail.shop(),customer_id=customer.id).one_or_none() if customer else None
    frozen={'customer_shopify_id':binding.shopify_customer_id if binding else None,'contract':remote.CONTRACT,'cart':c.payload,'quote':quote,'tenders':tender_plan,'policy':policy,'config_version':config_version,'location':db.get(POSRegister,s.register_id).location_id,'cashier':user.username,'customer_name':order.customer_name,'customer_phone':order.whatsapp_phone}
    record=POSCheckout(id=str(uuid4()),shop=retail.shop(),cart_id=c.id,sales_order_id=order.id,reference=reference,request_key=key,actor_id=user.id,session_id=s.id,
        frozen=frozen,frozen_hash=digest(frozen),status='AWAITING_PAYMENT' if any(t['method']=='BANK_TRANSFER' for t in tender_plan) else 'QUEUED')
    db.add(record);db.flush()
    for i,t in enumerate(tender_plan):
        payment,_=payments.record_payment_claim(db,order.id,{'payment_method':t['method'],'amount':t['amount'],'payment_date':datetime.now(ZoneInfo('Asia/Colombo')).date().isoformat(),'payment_reference':t['reference'] or None},user.username,'pos:'+record.id+':'+str(i),commit=False,pos_authorized=True)
        payment.source='POS'
        if t['confirmed']:payments.decide_payment(db,order.id,payment.id,approve=True,actor=user.username,idempotency_key='posverify:'+record.id+':'+str(i),commit=False,pos_authorized=True)
        if t['method']=='CASH':db.add(POSCashMovement(session_id=s.id,amount=t['amount'],kind='SALE_TENDER',reference='poscash:'+record.id+':'+str(i),actor_id=user.id,detail={'received':t['received'],'change':t['change'],'sale':record.reference,'authoritative_sale_pending':True}))
    c.status='CHECKOUT';c.version+=1
    if record.status=='QUEUED':queue_create(db,user,record)
    audit(db,user,reference,'CHECKOUT_FROZEN',checkout=record.id,hash=record.frozen_hash);db.commit();return record


def sale(db,user,checkout_id):
    require(user);r=db.query(POSCheckout).filter_by(shop=retail.shop(),id=checkout_id).with_for_update().one_or_none()
    if not r:raise HTTPException(404,'POS sale not found')
    return r


def queue_create(db,user,r):
    if db.query(POSOperation).filter_by(business_key='create:'+r.id).first():return
    db.add(POSOperation(id=str(uuid4()),shop=r.shop,checkout_id=r.id,kind='CREATE',business_key='create:'+r.id,status='QUEUED',actor_id=user.id,plan={'frozen_hash':r.frozen_hash,'contract':remote.CONTRACT}))


def verify_bank(db,user,checkout_id,payment_id,key):
    require(user,'admin',True);locked(db);r=sale(db,user,checkout_id)
    if r.status!='AWAITING_PAYMENT':raise HTTPException(409,'Sale is not awaiting bank verification')
    p=db.query(SalesPayment).filter_by(id=payment_id,sales_order_id=r.sales_order_id,payment_method='BANK_TRANSFER').one_or_none()
    if not p:raise HTTPException(404,'Bank tender not found')
    payments.decide_payment(db,r.sales_order_id,p.id,approve=True,actor=user.username,idempotency_key=key,commit=False,pos_authorized=True)
    if not db.query(SalesPayment).filter_by(sales_order_id=r.sales_order_id,verification_status='PENDING').first():r.status='PAYMENT_READY'
    audit(db,user,r.reference,'BANK_VERIFIED',payment_id=p.id);db.commit();return r


def finalize(db,user,checkout_id):
    require(user,'sell');locked(db);r=sale(db,user,checkout_id);session(db,user,r.session_id)
    if r.status!='PAYMENT_READY':raise HTTPException(409,'Verified tenders and cashier confirmation required')
    r.status='QUEUED';queue_create(db,user,r);audit(db,user,r.reference,'CASHIER_FINALIZE_CONFIRMED');db.commit();return r


def post_journal(db,reference,entries):
    old=db.query(POSPosting).filter_by(reference=reference).one_or_none()
    if old:return old.entry_id
    if sum((d-c for _,d,c in entries),Decimal(0))!=0:raise HTTPException(409,'Unbalanced POS posting blocked')
    entry=JournalEntry(date=datetime.now(ZoneInfo('Asia/Colombo')).date(),memo=reference);db.add(entry);db.flush()
    for account,debit,credit in entries:
        if debit or credit:db.add(JournalLine(entry_id=entry.id,account_id=account,description=reference,debit=debit,credit=credit))
    db.flush()
    db.add(POSPosting(reference=reference,entry_id=entry.id));db.flush();return entry.id


def publish_sale(db,r,observed):
    q=r.frozen['quote'];a=r.frozen['policy']['accounts'];order=db.get(SalesOrder,r.sales_order_id)
    entries=[(a['sales'],Decimal(0),Decimal(q['subtotal'])),(a['discount'],Decimal(q['discount_total']),Decimal(0)),(a['tax'],Decimal(0),Decimal(q['tax_total']))]
    for t in r.frozen['tenders']:entries.append((a[{'CASH':'cash','CARD_PAYMENT':'card','BANK_TRANSFER':'bank'}[t['method']]],Decimal(t['amount']),Decimal(0)))
    journal=post_journal(db,'POS-sale:'+r.id,entries)
    for p in db.query(SalesPayment).filter_by(sales_order_id=order.id):p.accounting_entry_id=journal
    order.shopify_order_id=observed['id'];order.shopify_order_name=observed['name'];order.shopify_lifecycle_status='ORDER_LINKED';order.shopify_financial_status=observed['displayFinancialStatus'];order.shopify_sync_status='SYNCED';order.fulfillment_status='FULFILLED';order.order_status='DELIVERED';order.payment_status='PAID';order.amount_paid=order.grand_total
    order.shopify_total_received=order.grand_total;order.shopify_total_outstanding=0;order.shopify_payment_currency='LKR';order.shopify_order_linked_at=datetime.utcnow()
    r.status='COMPLETE';r.completed_at=datetime.utcnow();audit(db,None,r.reference,'SALE_VERIFIED',shopify_order=observed['id'],journal_id=journal)


def order_wire(r):
    f=r.frozen;q=f['quote'];items=[]
    for s in q['segments']:
        moneybag=lambda value:{'shopMoney':{'amount':value,'currencyCode':'LKR'}}
        item={'variantId':s['variant_id'],'quantity':s['quantity'],'priceSet':moneybag(s['net_unit']),
            'requiresShipping':False,'taxable':s['taxable'],'properties':[{'name':'_pixiepinks_line','value':str(s['line'])},{'name':'_pixiepinks_segment','value':str(s['segment'])},{'name':'_pos_original_unit','value':s['gross_unit']},{'name':'_pos_discount_unit','value':calc.fmt(Decimal(s['gross_unit'])-Decimal(s['net_unit']))},{'name':'_pos_tax_unit','value':s['tax_unit']}]}
        if Decimal(s['tax_unit']):item['taxLines']=[{'title':q['tax_title'],'rate':float(Decimal(q['tax_rate'])/100),'priceSet':moneybag(calc.fmt(Decimal(s['tax_unit'])*s['quantity']))}]
        else:item['taxLines']=[]
        items.append(item)
    wire={'order':{'currency':'LKR','sourceIdentifier':r.reference,'tags':['pixiepinks-pos-'+r.reference],
        'note':'PixiePinks POS '+r.reference+'; net-price snapshot; CRM retains original prices, discounts and exact external tenders. '+f['cart'].get('invoice_details',''),
        'lineItems':items,'taxesIncluded':False,
        'fulfillment':{'locationId':f['location'],'notifyCustomer':False},
        'transactions':[{'amountSet':{'shopMoney':{'amount':q['grand_total'],'currencyCode':'LKR'}},'gateway':'manual','kind':'SALE','status':'SUCCESS','locationId':f['location']}]},
        'options':{'inventoryBehaviour':'DECREMENT_OBEYING_POLICY','sendReceipt':False,'sendFulfillmentReceipt':False}}
    if f.get('customer_shopify_id'):wire['order']['customer']={'toAssociate':{'id':f['customer_shopify_id']}}
    return wire


def verify_order(r,order):
    if not isinstance(order,dict) or not inventory.remote.gid(order.get('id'),'Order') or order.get('sourceIdentifier')!=r.reference or 'pixiepinks-pos-'+r.reference not in order.get('tags',[]) or order.get('cancelledAt'):raise remote.POSRemoteError('Order identity/correlation mismatch')
    if (order.get('customer') or {}).get('id')!=r.frozen.get('customer_shopify_id'):raise remote.POSRemoteError('Customer association mismatch')
    def amount(field):
        v=(order.get(field) or {}).get('shopMoney') or {}
        if v.get('currencyCode')!='LKR':raise remote.POSRemoteError('Order currency mismatch')
        return calc.money(v.get('amount'))
    if amount('totalPriceSet')!=calc.money(r.frozen['quote']['grand_total']) or amount('totalReceivedSet')!=amount('totalPriceSet') or amount('totalRefundedSet')!=0 or order.get('displayFinancialStatus')!='PAID':raise remote.POSRemoteError('Order financial outcome not verified')
    if amount('totalTaxSet')!=calc.money(r.frozen['quote']['tax_total']) or amount('totalDiscountsSet')!=0:raise remote.POSRemoteError('Tax or Shopify-native discount outcome mismatch')
    conn=order.get('lineItems') or {};rows=conn.get('nodes');page=conn.get('pageInfo') or {}
    if not isinstance(rows,list) or page.get('hasNextPage') is not False:raise remote.POSRemoteError('Order lines incomplete')
    expected={(str(s['line']),str(s['segment'])):s for s in r.frozen['quote']['segments']};seen=set();mapped={}
    for row in rows:
        props={a['key']:a['value'] for a in row.get('customAttributes',[])};key=(props.get('_pixiepinks_line'),props.get('_pixiepinks_segment'))
        s=expected.get(key);price=(row.get('originalUnitPriceSet') or {}).get('shopMoney') or {}
        if not s or key in seen or (row.get('variant') or {}).get('id')!=s['variant_id'] or row.get('quantity')!=s['quantity'] or price.get('currencyCode')!='LKR' or calc.money(price.get('amount'))!=calc.money(s['net_unit']):raise remote.POSRemoteError('Order line mismatch')
        seen.add(key);mapped[row['id']]=row['quantity']
    if seen!=set(expected):raise remote.POSRemoteError('Order lines mismatch')
    fulfilled={}
    fs=order.get('fulfillments')
    if not isinstance(fs,list) or not fs or len(fs)>=2:raise remote.POSRemoteError('Fulfillment unavailable')
    for fulfillment in fs:
        if fulfillment.get('status')!='SUCCESS' or (fulfillment.get('location') or {}).get('id')!=r.frozen['location']:raise remote.POSRemoteError('Store fulfillment not verified')
        lines=fulfillment.get('fulfillmentLineItems') or {}
        if (lines.get('pageInfo') or {}).get('hasNextPage') is not False:raise remote.POSRemoteError('Fulfillment lines incomplete')
        for l in lines.get('nodes',[]):
            lid=(l.get('lineItem') or {}).get('id')
            if type(l.get('quantity')) is not int or lid not in mapped:raise remote.POSRemoteError('Fulfillment line mismatch')
            fulfilled[lid]=fulfilled.get(lid,0)+l['quantity']
    if fulfilled!=mapped:raise remote.POSRemoteError('Fulfillment quantity mismatch')
    tx=order.get('transactions')
    if not isinstance(tx,list) or len(tx)>=20:raise remote.POSRemoteError('Payment transaction coverage incomplete')
    successful=[t for t in tx if t.get('kind') in {'SALE','CAPTURE'} and t.get('status')=='SUCCESS']
    if not successful or sum((calc.money(t['amountSet']['shopMoney']['amount']) for t in successful),Decimal(0))!=amount('totalReceivedSet') or any(t.get('gateway')!='manual' or t['amountSet']['shopMoney']['currencyCode']!='LKR' for t in successful):raise remote.POSRemoteError('Manual payment evidence mismatch')
    return order


def dispatch_one(sessions,client=None):
    if not settings.POS_ENABLED:return False
    api=client or remote.client
    with sessions() as db:
        locked(db)
        running=db.query(POSOperation).filter(POSOperation.shop==retail.shop(),POSOperation.status=='DISPATCHING').first()
        if running and running.dispatched_at<datetime.utcnow()-timedelta(minutes=2):
            running.status='UNCERTAIN';running.error='WORKER_INTERRUPTED';audit(db,None,running.id,'WORKER_INTERRUPTED');db.commit();return True
        if db.query(POSOperation).filter(POSOperation.shop==retail.shop(),POSOperation.status.in_(['DISPATCHING','UNCERTAIN','REVIEW'])).first():return False
        op=db.query(POSOperation).filter_by(shop=retail.shop(),status='QUEUED').order_by(POSOperation.updated_at).first()
        if not op:return False
        if (op.kind=='CREATE' and not settings.POS_CHECKOUT_WRITE_ENABLED) or (op.kind=='REFUND' and not settings.POS_REFUND_WRITE_ENABLED):return False
        r=db.get(POSCheckout,op.checkout_id);user=db.get(User,op.actor_id)
        try:
            require(user,'sell' if op.kind=='CREATE' else 'refund',op.kind=='REFUND')
            if r.frozen_hash!=digest(r.frozen) or r.frozen['contract']!=remote.CONTRACT or op.attempts:raise HTTPException(409,'POS frozen identity/attempt failure')
            cap=api.capabilities()
            if not cap['checkout_ready' if op.kind=='CREATE' else 'refund_ready']:raise HTTPException(409,'POS Shopify scopes/schema not verified')
            if op.kind=='CREATE':
                session(db,user,r.session_id)
                if cfg(db)[1]!=r.frozen['config_version']:raise HTTPException(409,'POS policy changed')
                for account in r.frozen['policy']['accounts'].values():
                    if not db.get(Account,account):raise HTTPException(409,'POS accounting account unavailable')
                observations=[]
                for line in r.frozen['quote']['lines']:
                    inventory.assert_identity(db,line);observed=api.inspect(line)
                    observations.append({'variant_id':line['variant_id'],'location':line['location_id'],**observed})
                    if calc.money(observed['price'])!=calc.money(line['unit_price']) or observed['taxable']!=line['taxable'] or type(observed.get('available')) is not int or observed['available']<line['quantity']:raise HTTPException(409,'Product/stock changed')
                order=db.get(SalesOrder,r.sales_order_id)
                if payments.verified_total(db,order.id)!=order.grand_total or db.query(SalesPayment).filter_by(sales_order_id=order.id,verification_status='PENDING').first():raise HTTPException(409,'Tenders are not fully verified')
                if r.frozen.get('customer_shopify_id'):api.customer(r.frozen['customer_shopify_id'])
                found=api.find(r.reference)
                if found:raise HTTPException(409,'Remote order already exists; reconcile instead of creating another')
                wire=order_wire(r)
            else:wire=refund_wire(db,r,op,api)
            op.wire=wire;op.status='DISPATCHING';op.attempts=1;op.dispatched_at=datetime.utcnow();op.updated_at=datetime.utcnow()
            opid=op.id;kind=op.kind;rid=r.id
            audit(db,user,op.id,'REMOTE_INTENT',wire=wire,hash=digest(wire),stock=observations if op.kind=='CREATE' else [],inventory_authority='Shopify order/fulfillment only');db.commit()
        except (HTTPException,remote.POSRemoteError,inventory.InventoryError):
            op.status='CONFLICT';op.error='POS_PREFLIGHT_CHANGED';r.status='REVIEW';audit(db,user,op.id,'PREFLIGHT_BLOCKED');db.commit();return True
    try:remote_id=api.create(wire) if kind=='CREATE' else api.refund(wire)
    except Exception:
        with sessions() as db:
            locked(db);op=db.get(POSOperation,opid);r=db.get(POSCheckout,rid);op.status='UNCERTAIN';op.error='REMOTE_OUTCOME_UNKNOWN';r.status='UNCERTAIN';audit(db,None,op.id,'REMOTE_UNCERTAIN',automatic_retry=False);db.commit()
        return True
    with sessions() as db:
        locked(db);op=db.get(POSOperation,opid);r=db.get(POSCheckout,rid)
        op.outcome={'remote_id':remote_id};audit(db,None,op.id,'REMOTE_RETURNED',remote_id=remote_id)
        try:
            with db.begin_nested():
                if kind=='CREATE':
                    observed=verify_order(r,api.read(remote_id));op.outcome={'remote_id':remote_id,'order':observed};publish_sale(db,r,observed)
                else:finish_refund(db,r,op,api,remote_id)
                op.status='VERIFIED';op.updated_at=datetime.utcnow()
                db.flush()
        except Exception:op.status='REVIEW';op.error='READBACK_OR_POSTING_UNAVAILABLE';r.status='REVIEW';audit(db,None,op.id,'READBACK_REVIEW')
        db.commit()
    return True


def reconcile(db,user,operation_id,notes,client=None):
    require(user,'admin',True);locked(db);op=db.query(POSOperation).filter_by(shop=retail.shop(),id=operation_id).with_for_update().one_or_none()
    if not op or op.status not in {'UNCERTAIN','REVIEW','CONFLICT'}:raise HTTPException(409,'Operation does not need read-only reconciliation')
    r=db.get(POSCheckout,op.checkout_id);api=client or remote.client;notes=inventory.text(notes,1000,True)
    if op.kind=='CREATE':
        found=api.find(r.reference)
        if len(found)!=1:raise HTTPException(409,'Exactly one authoritative matching order required; zero matches never permit a blind retry')
        observed=verify_order(r,api.read(found[0]['id']));op.outcome={'remote_id':observed['id'],'order':observed};publish_sale(db,r,observed)
    else:
        known=(op.outcome or {}).get('remote_id')
        if not known:
            order=db.get(SalesOrder,r.sales_order_id);evidence=api.read(order.shopify_order_id)
            if not evidence or evidence.get('id')!=order.shopify_order_id or evidence.get('sourceIdentifier')!=r.reference:raise HTTPException(409,'Refund order identity unavailable')
            matches=[f for f in evidence.get('refunds',[]) if (f.get('note') or '').startswith('PixiePinks POS refund '+op.id+' ')]
            if len(matches)!=1:raise HTTPException(409,'Exactly one source-correlated Shopify refund is required; no automatic resend')
            known=matches[0]['id']
        finish_refund(db,r,op,api,known)
    op.status='VERIFIED';op.error=None;audit(db,user,op.id,'RECONCILED_READ_ONLY',notes=notes);db.commit();return r


def cancel_checkout(db,user,checkout_id,notes):
    require(user,'void',True);locked(db);r=sale(db,user,checkout_id);session(db,user,r.session_id)
    operations=db.query(POSOperation).filter_by(checkout_id=r.id).all()
    if r.status=='CANCELLED':return r
    if r.status=='COMPLETE' or any(o.attempts or o.status not in {'QUEUED','CONFLICT'} for o in operations):raise HTTPException(409,'A remotely attempted sale requires reconciliation/refund, never local void')
    notes=inventory.text(notes,500,True)
    for op in operations:op.status='CANCELLED'
    for payment in db.query(SalesPayment).filter_by(sales_order_id=r.sales_order_id):
        previous=payment.verification_status;payment.verification_status='REJECTED';payment.rejected_by=user.username;payment.rejected_at=datetime.utcnow();payment.rejection_reason='Unsent POS void; supervisor confirms all accepted tender returned: '+notes
        payments._audit(db,payment,'POS_VOID_TENDER_RETURNED',previous,'REJECTED',user.username,'posvoid:'+str(payment.id),notes)
    for i,t in enumerate(r.frozen['tenders']):
        if t['method']=='CASH':db.add(POSCashMovement(session_id=r.session_id,amount=-Decimal(t['amount']),kind='VOID_CASH_RETURN',reference='posvoid:'+r.id+':'+str(i),actor_id=user.id,detail={'confirmation':inventory.text(notes,500,True)}))
    r.status='CANCELLED';order=db.get(SalesOrder,r.sales_order_id);order.order_status='CANCELLED';payments._derive_summary(db,order);audit(db,user,r.reference,'UNSENT_VOID',notes=notes,physical_cash_return_confirmed=True);db.commit();return r


def prepare_refund(db,user,checkout_id,session_id,key,items,method,restock,reason):
    require(user,'refund',True)
    if not settings.POS_REFUND_WRITE_ENABLED:raise HTTPException(409,'POS refunds disabled')
    locked(db);r=sale(db,user,checkout_id);session(db,user,session_id)
    if r.status!='COMPLETE':raise HTTPException(409,'Original verified receipt required')
    if method not in {'CASH','CARD_PAYMENT','BANK_TRANSFER'} or type(restock) is not bool or not isinstance(items,list) or not items or not isinstance(key,str) or not 16<=len(key)<=64:raise HTTPException(422,'Refund request, method and restock decision required')
    request_hash=digest({'checkout':r.id,'session':session_id,'items':items,'method':method,'restock':restock,'reason':reason})
    business='refund:'+r.id+':'+key
    old=db.query(POSOperation).filter_by(business_key=business).first()
    if old:
        if old.plan.get('request_hash')!=request_hash:raise HTTPException(409,'Refund request key already used for different data')
        return db.query(POSRefund).filter_by(operation_id=old.id).one()
    used={}
    for old in db.query(POSRefund).filter_by(checkout_id=r.id):
        for line in old.plan['items']:used[line['line']]=used.get(line['line'],0)+line['quantity']
    chosen=[];total=Decimal(0);net_total=Decimal(0);gross_total=Decimal(0);seen=set()
    for raw in items:
        if not isinstance(raw,dict) or set(raw)!={'line','quantity'} or type(raw['line']) is not int or raw['line'] in seen or not 0<=raw['line']<len(r.frozen['quote']['lines']):raise HTTPException(422,'Select original receipt lines')
        index=raw['line'];qty=inventory.amount(raw['quantity'],1,99);line=r.frozen['quote']['lines'][index];start=used.get(index,0)
        if start+qty>line['quantity']:raise HTTPException(409,'Return exceeds unreturned quantity (including pending returns)')
        units=[]
        for segment in line['segments']:
            units.extend([(segment['segment'],Decimal(segment['net_unit']),Decimal(segment['tax_unit']))]*segment['quantity'])
        selection=units[start:start+qty];amount=sum((n+t for _,n,t in selection),Decimal(0));net=sum((n for _,n,_ in selection),Decimal(0));gross=Decimal(line['unit_price'])*qty
        parts={}
        for seg,_,_ in selection:parts[str(seg)]=parts.get(str(seg),0)+1
        chosen.append({'line':index,'quantity':qty,'segments':parts,'amount':calc.fmt(amount),'net':calc.fmt(net),'gross':calc.fmt(gross)})
        total+=amount;net_total+=net;gross_total+=gross;seen.add(index)
    if total<=0:raise HTTPException(422,'Zero-value returns need a separately approved nonfinancial return policy')
    plan={'request_hash':request_hash,'items':chosen,'amount':calc.fmt(total),'net':calc.fmt(net_total),'gross':calc.fmt(gross_total),'method':method,'restock':restock,'reason':inventory.text(reason,500,True),'location':r.frozen['location']}
    op=POSOperation(id=str(uuid4()),shop=r.shop,checkout_id=r.id,kind='REFUND',business_key=business,status='QUEUED',actor_id=user.id,plan=plan)
    db.add(op);db.flush();refund=POSRefund(id=str(uuid4()),checkout_id=r.id,operation_id=op.id,session_id=session_id,plan=plan,status='APPROVED',approved_by=user.id);db.add(refund)
    audit(db,user,op.id,'REFUND_APPROVED',plan=plan);db.commit();return refund


def refund_wire(db,r,op,api):
    order=db.get(SalesOrder,r.sales_order_id);observed=api.read(order.shopify_order_id)
    if not observed or observed['id']!=order.shopify_order_id or observed.get('sourceIdentifier')!=r.reference or observed.get('cancelledAt'):raise remote.POSRemoteError('Refund order identity mismatch')
    nodes=(observed.get('lineItems') or {}).get('nodes',[])
    if (observed.get('lineItems') or {}).get('pageInfo',{}).get('hasNextPage') is not False:raise remote.POSRemoteError('Refund lines incomplete')
    mapping={}
    for line in nodes:
        attrs={p['key']:p['value'] for p in line.get('customAttributes',[])}
        mapping[(attrs.get('_pixiepinks_line'),attrs.get('_pixiepinks_segment'))]=line
    transactions=[t for t in observed.get('transactions',[]) if t.get('status')=='SUCCESS' and t.get('kind')=='SALE' and t.get('gateway')=='manual']
    if len(transactions)!=1 or len(observed.get('transactions',[]))>=20 or len(observed.get('refunds',[]))>=100:raise remote.POSRemoteError('Manual refund parent coverage unavailable')
    paid=calc.money(observed['totalReceivedSet']['shopMoney']['amount']);already=calc.money(observed['totalRefundedSet']['shopMoney']['amount'])
    if paid-already<calc.money(op.plan['amount']):raise remote.POSRemoteError('Refund exceeds remote refundable balance')
    lines=[]
    for chosen in op.plan['items']:
        for segment,quantity in chosen['segments'].items():
            line=mapping.get((str(chosen['line']),segment))
            if not line or type(line.get('refundableQuantity')) is not int or line['refundableQuantity']<quantity:raise remote.POSRemoteError('Refund quantity changed')
            raw={'lineItemId':line['id'],'quantity':quantity,'restockType':'RETURN' if op.plan['restock'] else 'NO_RESTOCK'}
            if op.plan['restock']:raw['locationId']=op.plan['location']
            lines.append(raw)
    return {'input':{'orderId':order.shopify_order_id,'currency':'LKR','note':'PixiePinks POS refund '+op.id+' '+op.plan['reason'],'notify':False,'refundLineItems':lines,
        'transactions':[{'orderId':order.shopify_order_id,'parentId':transactions[0]['id'],'gateway':'manual','kind':'REFUND','amount':op.plan['amount']}]},'key':op.id}


def finish_refund(db,r,op,api,refund_id):
    order=db.get(SalesOrder,r.sales_order_id);observed=api.read(order.shopify_order_id)
    if not observed or observed.get('id')!=order.shopify_order_id or observed.get('sourceIdentifier')!=r.reference or len(observed.get('refunds',[]))>=100:raise remote.POSRemoteError('Refund coverage unavailable')
    matches=[f for f in observed.get('refunds',[]) if f.get('id')==refund_id and op.id in (f.get('note') or '')]
    if len(matches)!=1 or matches[0]['totalRefundedSet']['shopMoney']['currencyCode']!='LKR' or calc.money(matches[0]['totalRefundedSet']['shopMoney']['amount'])!=calc.money(op.plan['amount']):raise remote.POSRemoteError('Refund amount/identity unverified')
    result=matches[0]
    if not op.wire:raise remote.POSRemoteError('Immutable refund intent unavailable')
    expected=op.wire['input'];line_connection=result.get('refundLineItems') or {};tx_connection=result.get('transactions') or {}
    if (line_connection.get('pageInfo') or {}).get('hasNextPage') is not False or (tx_connection.get('pageInfo') or {}).get('hasNextPage') is not False:raise remote.POSRemoteError('Refund line/transaction coverage incomplete')
    actual_lines=[{'lineItemId':v['lineItem']['id'],'quantity':v['quantity'],'restockType':v['restockType'],**({'locationId':v['location']['id']} if v.get('location') else {})} for v in line_connection.get('nodes',[])]
    if sorted(actual_lines,key=lambda v:v['lineItemId'])!=sorted(expected['refundLineItems'],key=lambda v:v['lineItemId']):raise remote.POSRemoteError('Refund lines/restock mismatch')
    tx=tx_connection.get('nodes',[]);parent=expected['transactions'][0]['parentId']
    if len(tx)!=1 or tx[0].get('kind')!='REFUND' or tx[0].get('status')!='SUCCESS' or tx[0].get('gateway')!='manual' or (tx[0].get('parentTransaction') or {}).get('id')!=parent or (tx[0].get('amountSet') or {}).get('shopMoney',{}).get('currencyCode')!='LKR' or calc.money((tx[0].get('amountSet') or {}).get('shopMoney',{}).get('amount'))!=calc.money(op.plan['amount']):raise remote.POSRemoteError('Successful manual refund transaction not verified')
    order.shopify_financial_status=observed.get('displayFinancialStatus');order.shopify_total_refunded=calc.money(observed['totalRefundedSet']['shopMoney']['amount'])
    op.outcome={'remote_id':refund_id,'refund':result};refund=db.query(POSRefund).filter_by(operation_id=op.id).one();refund.status='AWAITING_PAYOUT';r.status='COMPLETE'
    audit(db,None,op.id,'SHOPIFY_REFUND_VERIFIED',refund=matches[0],restock_owned_by='Shopify refund only')


def confirm_payout(db,user,refund_id,reference):
    require(user,'refund',True);locked(db);refund=db.get(POSRefund,refund_id)
    if not refund:raise HTTPException(404,'Refund not found')
    r=sale(db,user,refund.checkout_id)
    if refund.status=='PAID_OUT':return refund
    if refund.status!='AWAITING_PAYOUT':raise HTTPException(409,'Verified Shopify refund required before payout confirmation')
    s=session(db,user,refund.session_id);plan=refund.plan;value=Decimal(plan['amount']);method=plan['method']
    reference=inventory.text(reference,80,method!='CASH')
    if method=='CARD_PAYMENT' and 13<=len(re.sub(r'\D','',reference))<=19:raise HTTPException(422,'Do not enter card details')
    if method=='CASH':
        if drawer(db,s)<value:raise HTTPException(409,'Insufficient drawer cash')
        db.add(POSCashMovement(session_id=s.id,amount=-value,kind='REFUND',reference='posrefund:'+refund.id,actor_id=user.id,detail={'original_sale':r.reference}))
    a=r.frozen['policy']['accounts'];gross=Decimal(plan['gross']);net=Decimal(plan['net']);tax=value-net
    journal=post_journal(db,'POS-refund:'+refund.id,[(a['returns'],gross,Decimal(0)),(a['discount'],Decimal(0),gross-net),(a['tax'],tax,Decimal(0)),(a[{'CASH':'cash','CARD_PAYMENT':'card','BANK_TRANSFER':'bank'}[method]],Decimal(0),value)])
    refund.status='PAID_OUT';refund.payout_at=datetime.utcnow();audit(db,user,refund.id,'REFUND_PAYOUT_CONFIRMED',reference=reference,journal_id=journal,method=method);db.commit();return refund


def public_sale(db,r):
    order=db.get(SalesOrder,r.sales_order_id)
    return {'id':r.id,'reference':r.reference,'status':r.status,'frozen':r.frozen,'created_at':r.created_at.isoformat()+'Z','completed_at':r.completed_at.isoformat()+'Z' if r.completed_at else None,
        'shopify_order_id':order.shopify_order_id,'shopify_order_name':order.shopify_order_name,
        'payments':[{'id':p.id,'method':p.payment_method,'amount':str(p.amount),'status':p.verification_status,'reference':p.payment_reference} for p in db.query(SalesPayment).filter_by(sales_order_id=order.id)],
        'operations':[{'id':o.id,'kind':o.kind,'status':o.status,'attempts':o.attempts,'error':o.error,'outcome':o.outcome} for o in db.query(POSOperation).filter_by(checkout_id=r.id)],
        'refunds':[{'id':f.id,'status':f.status,'plan':f.plan} for f in db.query(POSRefund).filter_by(checkout_id=r.id)]}


def bind_customer(db,user,customer_id,shopify_customer_id,client=None):
    require(user,'admin');locked(db)
    if type(customer_id) is not int or not db.get(Customer,customer_id) or not inventory.remote.gid(shopify_customer_id,'Customer'):raise HTTPException(422,'Existing CRM and Shopify customer required')
    prior=db.query(POSCustomerBinding).filter_by(shop=retail.shop(),customer_id=customer_id).first()
    if prior:
        if prior.shopify_customer_id!=shopify_customer_id:raise HTTPException(409,'An immutable binding already exists')
        return prior
    (client or remote.client).customer(shopify_customer_id)
    row=POSCustomerBinding(shop=retail.shop(),customer_id=customer_id,shopify_customer_id=shopify_customer_id,actor_id=user.id);db.add(row)
    audit(db,user,customer_id,'EXISTING_CUSTOMER_BOUND',shopify_id=shopify_customer_id);db.commit();return row
