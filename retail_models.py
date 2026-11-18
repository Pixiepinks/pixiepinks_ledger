"""Additive retail master; Shopify values and local identifiers remain distinct."""
from datetime import datetime
from sqlalchemy import Column,Integer,String,Text,Boolean,DateTime,JSON,ForeignKey,UniqueConstraint,Numeric,Index
from database import Base

class RetailShop(Base):
    __tablename__='retail_shops'
    shop=Column(String(100),primary_key=True)
    sync_job_id=Column(Integer)
    last_success=Column(DateTime)
    watermark=Column(DateTime)

class RetailVariant(Base):
    __tablename__='retail_variants'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    product_id=Column(String(100),nullable=False,index=True)
    variant_id=Column(String(100),nullable=False)
    inventory_item_id=Column(String(100))
    title=Column(String(500),nullable=False,index=True)
    variant_title=Column(String(500),nullable=False,index=True)
    product_type=Column(String(200))
    category=Column(String(10))  # Explicit local mapping, never inferred from title.
    vendor=Column(String(200))
    sku=Column(String(500),index=True)
    barcode=Column(String(500),index=True)
    local_sku=Column(String(100),index=True)
    local_barcode=Column(String(100),index=True)
    barcode_source=Column(String(40),nullable=False,default='SHOPIFY')
    barcode_alternative_reason=Column(Text)
    price=Column(Numeric(18,2))
    compare_at_price=Column(Numeric(18,2))
    currency=Column(String(3))
    image=Column(Text)
    options=Column(JSON,nullable=False,default=list)
    collections=Column(JSON,nullable=False,default=list)
    collections_complete=Column(Boolean,nullable=False,default=False)
    product_status=Column(String(30),nullable=False)
    tracked=Column(Boolean)
    shopify_updated_at=Column(DateTime)
    synced_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    missing=Column(Boolean,nullable=False,default=False)
    stale=Column(Boolean,nullable=False,default=False)
    version=Column(Integer,nullable=False,default=1)
    __table_args__=(UniqueConstraint('shop','variant_id',name='uq_retail_shop_variant'),)

class RetailSyncJob(Base):
    __tablename__='retail_sync_jobs'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    actor=Column(String(100),nullable=False)
    mode=Column(String(20),nullable=False)
    status=Column(String(30),nullable=False,default='QUEUED',index=True)
    cursor=Column(Text)
    scanned=Column(Integer,nullable=False,default=0)
    counts=Column(JSON,nullable=False,default=dict)
    error=Column(String(300))
    started_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    heartbeat=Column(DateTime,nullable=False,default=datetime.utcnow)
    completed_at=Column(DateTime)

class RetailImportRow(Base):
    __tablename__='retail_import_rows'
    id=Column(Integer,primary_key=True)
    job_id=Column(Integer,ForeignKey('retail_sync_jobs.id'),nullable=False,index=True)
    variant_id=Column(String(100),nullable=False)
    data=Column(JSON,nullable=False)
    __table_args__=(UniqueConstraint('job_id','variant_id',name='uq_retail_import_variant'),)

class RetailCounter(Base):
    __tablename__='retail_counters'
    shop=Column(String(100),primary_key=True)
    kind=Column(String(20),primary_key=True)
    value=Column(Integer,nullable=False,default=0)

class RetailIdentifier(Base):
    __tablename__='retail_identifiers'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False)
    variant_id=Column(Integer,ForeignKey('retail_variants.id'),nullable=False,index=True)
    kind=Column(String(20),nullable=False)
    value=Column(String(100),nullable=False)
    actor=Column(String(100),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    __table_args__=(UniqueConstraint('shop','kind','value',name='uq_retail_allocated_identifier'),)

class RetailPreview(Base):
    __tablename__='retail_previews'
    token=Column(String(40),primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    kind=Column(String(20),nullable=False)
    payload=Column(JSON,nullable=False)
    expires_at=Column(DateTime,nullable=False)
    committed=Column(Boolean,nullable=False,default=False)

class RetailAudit(Base):
    __tablename__='retail_audits'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    variant_id=Column(Integer,ForeignKey('retail_variants.id'),index=True)
    actor=Column(String(100),nullable=False)
    action=Column(String(40),nullable=False)
    detail=Column(JSON,nullable=False,default=dict)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class RetailException(Base):
    __tablename__='retail_exceptions'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    kind=Column(String(20),nullable=False)
    value=Column(String(500),nullable=False)
    variant_ids=Column(JSON,nullable=False)
    reason=Column(String(500),nullable=False)
    actor=Column(String(100),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class RetailLabelTemplate(Base):
    __tablename__='retail_label_templates'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    title=Column(String(100),nullable=False)
    config=Column(JSON,nullable=False)
    version=Column(Integer,nullable=False,default=1)

class RetailPrintJob(Base):
    __tablename__='retail_print_jobs'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    parent_id=Column(Integer,ForeignKey('retail_print_jobs.id'))
    status=Column(String(30),nullable=False,default='QUEUED',index=True)
    template=Column(JSON,nullable=False)
    rows=Column(JSON,nullable=False)
    total=Column(Integer,nullable=False)
    error=Column(String(300))
    file_ref=Column(String(40))
    sha256=Column(String(64))
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    generated_at=Column(DateTime)
    downloaded_at=Column(DateTime)
    printed_at=Column(DateTime)
    claimed_at=Column(DateTime)


class RetailBarcodeJob(Base):
    __tablename__='retail_barcode_jobs'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    actor=Column(String(100),nullable=False)
    token=Column(String(40),nullable=False,unique=True)
    status=Column(String(30),nullable=False,default='PREVIEW',index=True)
    api_version=Column(String(20),nullable=False)
    digest=Column(String(64),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    expires_at=Column(DateTime,nullable=False)
    approved_at=Column(DateTime)
    completed_at=Column(DateTime)
    canceled_at=Column(DateTime)


class RetailBarcodeItem(Base):
    __tablename__='retail_barcode_items'
    id=Column(Integer,primary_key=True)
    job_id=Column(Integer,ForeignKey('retail_barcode_jobs.id'),nullable=False,index=True)
    variant_id=Column(Integer,ForeignKey('retail_variants.id'),nullable=False)
    idempotency_key=Column(String(64),nullable=False,unique=True)
    snapshot=Column(JSON,nullable=False)
    before=Column(JSON,nullable=False,default=dict)
    after=Column(JSON)
    status=Column(String(30),nullable=False)
    reason=Column(String(100),nullable=False)
    attempts=Column(Integer,nullable=False,default=0)
    next_attempt_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    checked_at=Column(DateTime)
    sent_at=Column(DateTime)
    verified_at=Column(DateTime)
    lease_until=Column(DateTime)
    claim_token=Column(String(40))
    __table_args__=(UniqueConstraint('job_id','variant_id',name='uq_retail_barcode_job_variant'),)


class RetailBarcodeReservation(Base):
    __tablename__='retail_barcode_reservations'
    shop=Column(String(100),primary_key=True)
    variant_id=Column(Integer,ForeignKey('retail_variants.id'),primary_key=True)
    value=Column(String(100),nullable=False)
    item_id=Column(Integer,ForeignKey('retail_barcode_items.id'),nullable=False,unique=True)
    __table_args__=(UniqueConstraint('shop','value',name='uq_retail_barcode_reserved_value'),)


class RetailBarcodeEvent(Base):
    __tablename__='retail_barcode_events'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    job_id=Column(Integer,ForeignKey('retail_barcode_jobs.id'),nullable=False,index=True)
    item_id=Column(Integer,ForeignKey('retail_barcode_items.id'),index=True)
    actor=Column(String(100),nullable=False)
    action=Column(String(40),nullable=False)
    detail=Column(JSON,nullable=False,default=dict)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
