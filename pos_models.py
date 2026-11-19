"""POS orchestration references the existing SalesOrder/SalesPayment financial records."""
from datetime import datetime
from sqlalchemy import Column,Integer,String,DateTime,JSON,ForeignKey,Numeric,UniqueConstraint
from database import Base

class POSConfig(Base):
    __tablename__='pos_config'
    shop=Column(String(100),primary_key=True)
    value=Column(JSON,nullable=False)
    version=Column(Integer,nullable=False,default=1)

class POSRegister(Base):
    __tablename__='pos_registers'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    name=Column(String(80),nullable=False)
    location_id=Column(String(100),nullable=False)
    __table_args__=(UniqueConstraint('shop','name',name='uq_pos_register_name'),)

class POSSession(Base):
    __tablename__='pos_sessions'
    id=Column(String(36),primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    register_id=Column(Integer,ForeignKey('pos_registers.id'),nullable=False)
    active_register_id=Column(Integer,ForeignKey('pos_registers.id'),unique=True)
    cashier_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    active_cashier_id=Column(Integer,ForeignKey('users.id'),unique=True)
    opening_float=Column(Numeric(14,2),nullable=False)
    status=Column(String(20),nullable=False,default='OPEN')
    opened_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    closed_at=Column(DateTime)
    closing=Column(JSON)

class POSCart(Base):
    __tablename__='pos_carts'
    id=Column(String(36),primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    session_id=Column(String(36),ForeignKey('pos_sessions.id'),nullable=False)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    status=Column(String(25),nullable=False,default='DRAFT')
    version=Column(Integer,nullable=False,default=1)
    payload=Column(JSON,nullable=False)
    quote=Column(JSON,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class POSCheckout(Base):
    __tablename__='pos_checkouts'
    id=Column(String(36),primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    cart_id=Column(String(36),ForeignKey('pos_carts.id'),nullable=False,unique=True)
    sales_order_id=Column(Integer,ForeignKey('sales_orders.id'),nullable=False,unique=True)
    reference=Column(String(50),nullable=False,unique=True)
    request_key=Column(String(64),nullable=False)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    session_id=Column(String(36),ForeignKey('pos_sessions.id'),nullable=False)
    frozen=Column(JSON,nullable=False)
    frozen_hash=Column(String(64),nullable=False)
    status=Column(String(30),nullable=False,index=True)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    completed_at=Column(DateTime)
    __table_args__=(UniqueConstraint('shop','request_key',name='uq_pos_checkout_request'),)

class POSOperation(Base):
    __tablename__='pos_operations'
    id=Column(String(36),primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    checkout_id=Column(String(36),ForeignKey('pos_checkouts.id'),nullable=False,index=True)
    kind=Column(String(20),nullable=False)
    business_key=Column(String(100),nullable=False,unique=True)
    status=Column(String(30),nullable=False,index=True)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    plan=Column(JSON,nullable=False)
    wire=Column(JSON(none_as_null=True))
    outcome=Column(JSON(none_as_null=True))
    attempts=Column(Integer,nullable=False,default=0)
    dispatched_at=Column(DateTime)
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    error=Column(String(100))

class POSRefund(Base):
    __tablename__='pos_refunds'
    id=Column(String(36),primary_key=True)
    checkout_id=Column(String(36),ForeignKey('pos_checkouts.id'),nullable=False,index=True)
    operation_id=Column(String(36),ForeignKey('pos_operations.id'),nullable=False,unique=True)
    session_id=Column(String(36),ForeignKey('pos_sessions.id'),nullable=False)
    plan=Column(JSON,nullable=False)
    status=Column(String(30),nullable=False)
    approved_by=Column(Integer,ForeignKey('users.id'),nullable=False)
    payout_at=Column(DateTime)

class POSCashMovement(Base):
    __tablename__='pos_cash_movements'
    id=Column(Integer,primary_key=True)
    session_id=Column(String(36),ForeignKey('pos_sessions.id'),nullable=False,index=True)
    amount=Column(Numeric(14,2),nullable=False)
    kind=Column(String(25),nullable=False)
    reference=Column(String(100),nullable=False,unique=True)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    detail=Column(JSON,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class POSAudit(Base):
    __tablename__='pos_audits'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    reference=Column(String(100),nullable=False,index=True)
    action=Column(String(40),nullable=False)
    actor_id=Column(Integer,ForeignKey('users.id'))
    detail=Column(JSON,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class POSPosting(Base):
    __tablename__='pos_postings'
    id=Column(Integer,primary_key=True)
    reference=Column(String(100),nullable=False,unique=True)
    entry_id=Column(Integer,ForeignKey('journal_entries.id'),nullable=False,unique=True)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class POSCustomerBinding(Base):
    __tablename__='pos_customer_bindings'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    customer_id=Column(Integer,ForeignKey('customers.id'),nullable=False)
    shopify_customer_id=Column(String(100),nullable=False)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    __table_args__=(UniqueConstraint('shop','customer_id',name='uq_pos_customer_binding'),UniqueConstraint('shop','shopify_customer_id',name='uq_pos_shopify_customer_binding'))
