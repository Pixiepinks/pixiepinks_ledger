"""Shopify inventory workflow records; quantities here are observations, never stock authority."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey, UniqueConstraint
from database import Base


class InventoryDocument(Base):
    __tablename__ = 'inventory_documents'
    id = Column(String(36), primary_key=True)
    shop = Column(String(100), nullable=False, index=True)
    client_key = Column(String(64), nullable=False)
    kind = Column(String(20), nullable=False)
    business_key = Column(String(64))
    reference = Column(String(50), nullable=False, unique=True)
    payload = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    status = Column(String(30), nullable=False, default='DRAFT', index=True)
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    approved_by = Column(Integer, ForeignKey('users.id'))
    approval_hash = Column(String(64))
    contract_version = Column(String(60))
    baseline = Column(JSON(none_as_null=True))
    plan = Column(JSON(none_as_null=True))
    dispatch_key = Column(String(36), nullable=False, unique=True)
    attempts = Column(Integer, nullable=False, default=0)
    dispatched_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    error = Column(String(100))
    remote_reference = Column(String(150))
    __table_args__ = (UniqueConstraint('shop','client_key',name='uq_inventory_client_key'),
        UniqueConstraint('shop','business_key',name='uq_inventory_business_key'))


class InventoryEvent(Base):
    __tablename__ = 'inventory_events'
    id = Column(Integer, primary_key=True)
    shop = Column(String(100), nullable=False, index=True)
    document_id = Column(String(36), ForeignKey('inventory_documents.id'), nullable=False, index=True)
    action = Column(String(40), nullable=False)
    actor_id = Column(Integer, ForeignKey('users.id'))
    detail = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class InventoryObservation(Base):
    __tablename__ = 'inventory_count_observations'
    id = Column(Integer, primary_key=True)
    document_id = Column(String(36), ForeignKey('inventory_documents.id'), nullable=False, index=True)
    line = Column(Integer, nullable=False)
    quantity = Column(Integer, nullable=False)
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class InventoryPolicy(Base):
    __tablename__ = 'inventory_policies'
    id = Column(Integer, primary_key=True)
    shop = Column(String(100), nullable=False, index=True)
    variant_id = Column(Integer, ForeignKey('retail_variants.id'), nullable=False)
    location_id = Column(String(100), nullable=False)
    minimum = Column(Integer, nullable=False)
    target = Column(Integer, nullable=False)
    supplier_id = Column(Integer, ForeignKey('suppliers.id'))
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('shop','variant_id','location_id',name='uq_inventory_policy'),)


class InventoryPolicyEvent(Base):
    __tablename__ = 'inventory_policy_events'
    id = Column(Integer, primary_key=True)
    shop = Column(String(100), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    detail = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
