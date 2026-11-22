"""Admin-only synthetic checkout orchestration, outside every financial POS queue.

Domain writes are POSReceiptTest, HardwareJob and existing HardwareEvent audit;
the existing shop metadata lock is reused.
No POS checkout, order, payment, accounting or Shopify service is called here.
"""
import re
from types import SimpleNamespace
from uuid import uuid4
from fastapi import HTTPException
from hardware_models import POSReceiptTest, HardwareJob
from settings import settings
import hardware_service as hardware
import pos_receipts


def require(user):
    hardware.require(user,'admin')
    if not settings.POS_TEST_CHECKOUT_ENABLED:
        raise HTTPException(404,'Test checkout mode disabled')


def eligible_device(db,did):
    d=hardware.device(db,did)
    c=d.config
    if not settings.POS_LOCAL_PRINT_ENABLED or not settings.PRINT_CONNECTOR_ENCRYPTED_ENABLED:
        raise HTTPException(409,'Encrypted local receipt printing required')
    if (d.status!='ACTIVE' or d.active_register_id!=d.register_id or
        not c.get('receipt_only') or c.get('receipt_profile')!='80' or
        c.get('receipt_queue')!=hardware.RECEIPT_QUEUE or c.get('drawer_queue') or
        c.get('drawer_verified') or c.get('auto_cash_movements')):
        raise HTTPException(409,'Active dedicated 80 mm receipt-only connector required')
    return d


def snapshot(user):
    # Fixed synthetic fixture: no live catalog/customer/account lookup and no tender.
    return {'cashier':user.username,'customer_name':'SYNTHETIC TEST CUSTOMER',
        'customer_phone':'','cart':{},'policy':{'tax_title':'Test tax (0%)'},
        'quote':{'lines':[{'title':'Synthetic acceptance item','variant_title':'TEST ONLY',
            'quantity':1,'unit_price':'400.00','total':'350.00','line_discount':'50.00',
            'order_discount':'0.00'}], 'subtotal':'400.00','order_discount':'0.00',
            'discount_total':'50.00','tax_total':'0.00','grand_total':'350.00'},
        'tenders':[{'method':'CASH','amount':'350.00','received':'500.00','change':'150.00','reference':'SIMULATED — NO MONEY RECEIVED'}]}


def find(db,user,tid):
    require(user);hardware.pos.locked(db)
    row=db.query(POSReceiptTest).filter_by(id=tid,shop=hardware.retail.shop()).with_for_update().one_or_none()
    if not row:raise HTTPException(404,'Test checkout not found')
    return row


def create(db,user,device_id,key):
    require(user);hardware.pos.locked(db)
    if not isinstance(key,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,64}',key):
        raise HTTPException(422,'Stable test request key required')
    prior=db.query(POSReceiptTest).filter_by(shop=hardware.retail.shop(),request_key=key).one_or_none()
    if prior:
        if prior.device_id!=device_id or prior.actor_id!=user.id:
            raise HTTPException(409,'Test request key belongs to another request')
        return prior
    d=eligible_device(db,device_id);frozen=snapshot(user)
    row=POSReceiptTest(id=str(uuid4()),shop=d.shop,request_key=key,device_id=d.id,
        device_version=d.version,actor_id=user.id,frozen=frozen,frozen_hash=hardware.pos.digest(frozen),status='DRAFT')
    db.add(row);db.flush()
    hardware.event(db,d,'TEST_CHECKOUT_CREATED',user,test_checkout_id=row.id)
    db.commit();return row


def finalize(db,user,tid,confirm):
    row=find(db,user,tid)
    if confirm is not True:raise HTTPException(422,'Explicit non-financial print confirmation required')
    # The checkout UUID is the finalization key. Never regenerate on retry, even
    # after expiry, config changes, uncertainty, a failed render/job or a restart.
    if row.status=='FINALIZED':return row
    if row.status!='DRAFT' or row.job_id:raise HTTPException(409,'Test checkout state unavailable')
    if hardware.pos.digest(row.frozen)!=row.frozen_hash:raise HTTPException(409,'Test snapshot changed')
    d=eligible_device(db,row.device_id)
    if d.version!=row.device_version:raise HTTPException(409,'Device changed; create a separately authorized new test')
    if d.config.get('printing_paused'):
        raise HTTPException(409,'Connector paused; test does not bypass hardware controls')
    # Isolated from POSOperation REVIEW, never from this printer's uncertainty.
    if db.query(HardwareJob).filter(HardwareJob.device_id==d.id,HardwareJob.status.in_(['QUEUED','DELIVERED_TO_AGENT','UNKNOWN'])).first():
        raise HTTPException(409,'Inspect existing connector work; test will not bypass or replay it')
    raw=pos_receipts.render(SimpleNamespace(frozen=row.frozen,reference='TEST-'+row.id,
        created_at=row.created_at),'80',test=True)
    job=hardware.add_job(db,d,'TEST','receipt:test-checkout:'+row.id,raw,user,profile='80',
        reason='Synthetic checkout acceptance — not a sale',automatic=True,test_checkout_id=row.id)
    row.job_id=job.id;row.status='FINALIZED';row.finalized_at=hardware.now()
    hardware.event(db,d,'TEST_CHECKOUT_FINALIZED',user,j=job,test_checkout_id=row.id)
    hardware.event(db,d,'TEST_RECEIPT_QUEUED',user,j=job,test_checkout_id=row.id)
    db.commit();return row


def valid_source(db,job):
    row=db.get(POSReceiptTest,job.detail.get('test_checkout_id'))
    return bool(row and row.status=='FINALIZED' and row.job_id==job.id and
        row.shop==job.shop and row.device_id==job.device_id and row.device_version==job.device_version and
        hardware.pos.digest(row.frozen)==row.frozen_hash and job.kind=='TEST' and
        job.business_key=='receipt:test-checkout:'+row.id and job.detail.get('test_only') is True and
        job.checkout_id is None and job.session_id is None and job.label_id is None and job.parent_id is None)


def public(db,row):
    job=db.get(HardwareJob,row.job_id) if row.job_id else None
    return {'id':row.id,'reference':'TEST-'+row.id,'mode':'TEST','status':row.status,
        'device_id':row.device_id,'device_version':row.device_version,
        'created_at':row.created_at.isoformat()+'Z',
        'finalized_at':row.finalized_at.isoformat()+'Z' if row.finalized_at else None,
        'simulated_total':'350.00','currency':'LKR','real_payment_received':False,
        'receipt':hardware.public_job(job) if job else None}
