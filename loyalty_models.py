"""Additive loyalty records reference existing customers, variants and commercial orders."""
from datetime import datetime
from sqlalchemy import Column,Integer,String,DateTime,ForeignKey,JSON,Numeric,Boolean,UniqueConstraint,CheckConstraint
from database import Base

class LoyaltyConfig(Base):
    __tablename__='loyalty_config'
    id=Column(Integer,primary_key=True)
    enabled=Column(Boolean,nullable=False,default=False,server_default='0')
    version=Column(Integer,nullable=False,default=1,server_default='1')
    value=Column(JSON,nullable=False)
    __table_args__=(CheckConstraint('id=1',name='ck_loyalty_single_config'),)

class LoyaltyMember(Base):
    __tablename__='loyalty_members'
    id=Column(Integer,primary_key=True,autoincrement=True)
    customer_id=Column(Integer,ForeignKey('customers.id'),nullable=False,unique=True)
    status=Column(String(16),nullable=False,default='PENDING',index=True)
    activated_at=Column(DateTime)
    first_order_id=Column(Integer,ForeignKey('sales_orders.id'))
    qualifying_count=Column(Integer,nullable=False,default=0)
    lifetime_net=Column(Numeric(18,2),nullable=False,default=0)
    lifetime_savings=Column(Numeric(18,2),nullable=False,default=0)
    last_purchase_at=Column(DateTime)
    source=Column(String(32),nullable=False,default='ORDER_EVENT')
    shopify_customer_id=Column(String(100),unique=True)
    identity_fingerprint=Column(String(64))
    registered_mobile=Column(String(40),index=True)
    suspension_reason=Column(String(1000))
    notes=Column(String(4000))
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    __table_args__=(CheckConstraint("status IN ('PENDING','ACTIVE','SUSPENDED','INELIGIBLE')",name='ck_loyalty_member_status'),CheckConstraint('qualifying_count>=0 AND lifetime_net>=0 AND lifetime_savings>=0',name='ck_loyalty_member_totals'),)
    @property
    def membership_id(self):return f'PP-L-{self.id:06d}'

class LoyaltyRule(Base):
    __tablename__='loyalty_rules'
    id=Column(Integer,primary_key=True)
    variant_id=Column(Integer,ForeignKey('retail_variants.id'),nullable=False,index=True)
    channel=Column(String(16),nullable=False,default='CRM')
    method=Column(String(16),nullable=False)
    value=Column(Numeric(18,2),nullable=False)
    cost=Column(Numeric(18,2))
    cost_reference=Column(String(500))
    cost_verified_at=Column(DateTime)
    variable_cost=Column(Numeric(18,2))
    starts_at=Column(DateTime,nullable=False)
    ends_at=Column(DateTime,nullable=False)
    enabled=Column(Boolean,nullable=False,default=False)
    max_quantity=Column(Numeric(14,3))
    exception_reason=Column(String(1000))
    exception_actor_id=Column(Integer,ForeignKey('users.id'))
    notes=Column(String(2000))
    version=Column(Integer,nullable=False,default=1)
    created_by=Column(Integer,ForeignKey('users.id'),nullable=False)
    updated_by=Column(Integer,ForeignKey('users.id'),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    __table_args__=(CheckConstraint("method IN ('PERCENT','AMOUNT','PRICE') AND channel='CRM'",name='ck_loyalty_rule_method'),CheckConstraint("value>=0 AND (method!='PRICE' OR value>0) AND (method!='PERCENT' OR value<=100) AND (cost IS NULL OR cost>=0) AND (variable_cost IS NULL OR variable_cost>=0) AND ends_at>starts_at AND (max_quantity IS NULL OR max_quantity>0)",name='ck_loyalty_rule_values'),)

class LoyaltyRuleVersion(Base):
    __tablename__='loyalty_rule_versions'
    id=Column(Integer,primary_key=True)
    rule_id=Column(Integer,ForeignKey('loyalty_rules.id'),nullable=False)
    version=Column(Integer,nullable=False)
    value=Column(JSON,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    __table_args__=(UniqueConstraint('rule_id','version',name='uq_loyalty_rule_version'),)

class LoyaltyPricing(Base):
    __tablename__='loyalty_order_pricing'
    order_id=Column(Integer,ForeignKey('sales_orders.id'),primary_key=True)
    value=Column(JSON,nullable=False)
    digest=Column(String(64),nullable=False)
    frozen=Column(Boolean,nullable=False,default=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class LoyaltyRedemption(Base):
    __tablename__='loyalty_redemptions'
    id=Column(Integer,primary_key=True)
    order_id=Column(Integer,ForeignKey('sales_orders.id'),nullable=False,index=True)
    line_id=Column(Integer,ForeignKey('sales_order_lines.id'),nullable=False,unique=True)
    member_id=Column(Integer,ForeignKey('loyalty_members.id'),nullable=False,index=True)
    rule_id=Column(Integer,ForeignKey('loyalty_rules.id'),nullable=False)
    rule_version=Column(Integer,nullable=False)
    snapshot=Column(JSON,nullable=False)
    discount=Column(Numeric(18,2),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    __table_args__=(CheckConstraint('discount>0',name='ck_loyalty_redemption_discount'),)

class LoyaltyRedemptionEvent(Base):
    __tablename__='loyalty_redemption_events'
    id=Column(Integer,primary_key=True)
    redemption_id=Column(Integer,ForeignKey('loyalty_redemptions.id'),nullable=False,index=True)
    event_key=Column(String(160),nullable=False,unique=True)
    kind=Column(String(24),nullable=False)
    amount=Column(Numeric(18,2),nullable=False)
    detail=Column(JSON,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)
    __table_args__=(CheckConstraint("kind IN ('PENDING','FINALIZED','REVERSAL','RELEASED')",name='ck_loyalty_redemption_event'),)

class LoyaltyAudit(Base):
    __tablename__='loyalty_audits'
    id=Column(Integer,primary_key=True)
    kind=Column(String(50),nullable=False,index=True)
    resource=Column(String(100),nullable=False,index=True)
    actor=Column(String(100),nullable=False)
    reason=Column(String(2000))
    previous=Column(JSON)
    value=Column(JSON)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class LoyaltyIdentityReview(Base):
    __tablename__='loyalty_identity_reviews'
    id=Column(Integer,primary_key=True)
    key=Column(String(150),nullable=False,unique=True)
    order_id=Column(Integer,ForeignKey('sales_orders.id'))
    customer_id=Column(Integer,ForeignKey('customers.id'))
    candidates=Column(JSON,nullable=False,default=list)
    status=Column(String(16),nullable=False,default='OPEN')
    reason=Column(String(1000),nullable=False)
    resolution=Column(JSON)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class LoyaltyOrderIdentity(Base):
    __tablename__='loyalty_order_identities'
    order_id=Column(Integer,ForeignKey('sales_orders.id'),primary_key=True)
    customer_id=Column(Integer,ForeignKey('customers.id'),nullable=False,index=True)
    reason=Column(String(2000),nullable=False)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)

class LoyaltyMigration(Base):
    __tablename__='loyalty_migrations'
    id=Column(String(36),primary_key=True)
    request_key=Column(String(100),nullable=False,unique=True)
    filters=Column(JSON,nullable=False)
    status=Column(String(20),nullable=False)
    digest=Column(String(64),nullable=False)
    counts=Column(JSON,nullable=False)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    approved_by=Column(Integer,ForeignKey('users.id'))
    approved_at=Column(DateTime)
    approval_reason=Column(String(2000))
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class LoyaltyMigrationRow(Base):
    __tablename__='loyalty_migration_rows'
    id=Column(Integer,primary_key=True)
    migration_id=Column(String(36),ForeignKey('loyalty_migrations.id'),nullable=False,index=True)
    customer_id=Column(Integer,ForeignKey('customers.id'))
    order_id=Column(Integer,ForeignKey('sales_orders.id'))
    status=Column(String(20),nullable=False)
    detail=Column(JSON,nullable=False)
    __table_args__=(UniqueConstraint('migration_id','customer_id',name='uq_loyalty_migration_customer'),)

class LoyaltyRecalculation(Base):
    __tablename__='loyalty_recalculations'
    customer_id=Column(Integer,ForeignKey('customers.id'),primary_key=True)
    status=Column(String(16),nullable=False,default='PENDING')
    attempts=Column(Integer,nullable=False,default=0)
    error=Column(String(60))
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class LoyaltyOrderExclusion(Base):
    """Management designation only; never edits or cancels the commercial order."""
    __tablename__='loyalty_order_exclusions'
    order_id=Column(Integer,ForeignKey('sales_orders.id'),primary_key=True)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    reason=Column(String(2000),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
