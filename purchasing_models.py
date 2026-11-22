"""Purchasing records reference the existing catalogue and inventory outbox."""
from datetime import datetime
from sqlalchemy import Column,String,Integer,DateTime,JSON,ForeignKey,CheckConstraint,UniqueConstraint
from database import Base

class PurchaseRecord(Base):
    __tablename__='purchase_records'
    id=Column(String(36),primary_key=True)
    shop=Column(String(255),nullable=False,index=True)
    kind=Column(String(20),nullable=False)
    parent_id=Column(String(36),ForeignKey('purchase_records.id'))
    supplier_id=Column(Integer,ForeignKey('suppliers.id'))
    client_key=Column(String(64),nullable=False)
    business_key=Column(String(255))
    request_hash=Column(String(64),nullable=False)
    payload=Column(JSON,nullable=False)
    status=Column(String(24),nullable=False,default='DRAFT')
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    approved_by=Column(Integer,ForeignKey('users.id'))
    version=Column(Integer,nullable=False,default=1)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    __table_args__=(UniqueConstraint('shop','client_key'),UniqueConstraint('shop','business_key'),CheckConstraint("kind IN ('BILL','RECEIPT','PAYMENT','SUPPLIER','LOCATION')",name='purchase_record_kind'),CheckConstraint("status IN ('DRAFT','REVIEW','APPROVED','VOID','RECORDED','ACTIVE')",name='purchase_record_status'))

class PurchaseMovement(Base):
    __tablename__='purchase_movements'
    id=Column(String(36),primary_key=True)
    receipt_id=Column(String(36),ForeignKey('purchase_records.id'),nullable=False)
    bill_line=Column(Integer,nullable=False)
    accepted=Column(Integer,nullable=False)
    damaged=Column(Integer,nullable=False)
    rejected=Column(Integer,nullable=False)
    document_id=Column(String(36),ForeignKey('inventory_documents.id'),unique=True)
    __table_args__=(UniqueConstraint('receipt_id','bill_line'),CheckConstraint('accepted >= 0 AND damaged >= 0 AND rejected >= 0 AND accepted + damaged + rejected > 0',name='purchase_received_quantities'),CheckConstraint('(accepted > 0 AND document_id IS NOT NULL) OR (accepted = 0 AND document_id IS NULL)',name='purchase_accepted_document'))

class PurchaseAudit(Base):
    __tablename__='purchase_audits'
    id=Column(Integer,primary_key=True)
    shop=Column(String(255),nullable=False,index=True)
    record_id=Column(String(36))
    actor_id=Column(Integer,ForeignKey('users.id'))
    action=Column(String(40),nullable=False)
    detail=Column(JSON,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
