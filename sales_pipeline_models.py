"""Additive sales commitments; no financial posting or external writes."""
from datetime import datetime
from sqlalchemy import Column,Integer,String,Date,Numeric,DateTime,ForeignKey,JSON,UniqueConstraint,Index
from database import Base

class SalesOpportunity(Base):
    __tablename__='sales_opportunities'
    id=Column(Integer,primary_key=True)
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id'),nullable=False,index=True)
    active_conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id'),unique=True)
    lead_id=Column(Integer,ForeignKey('leads.id'),nullable=False,index=True)
    order_id=Column(Integer,ForeignKey('sales_orders.id'),unique=True,index=True)
    payment_date=Column(Date,index=True)
    purchase_date=Column(Date,index=True)
    followup_date=Column(Date,index=True)
    estimated=Column(Numeric(14,2))
    agreed=Column(Numeric(14,2))
    data=Column(JSON,nullable=False,default=dict)
    version=Column(Integer,nullable=False,default=0)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class SalesPipelineEvent(Base):
    __tablename__='sales_pipeline_events'
    id=Column(Integer,primary_key=True)
    opportunity_id=Column(Integer,ForeignKey('sales_opportunities.id'),nullable=True,index=True)
    actor_id=Column(Integer,ForeignKey('users.id'))
    previous=Column(JSON,nullable=False)
    current=Column(JSON,nullable=False)
    key=Column(String(100),nullable=False,unique=True)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class SalesPipelineReminder(Base):
    __tablename__='sales_pipeline_reminders'
    id=Column(Integer,primary_key=True)
    opportunity_id=Column(Integer,ForeignKey('sales_opportunities.id'),nullable=False,index=True)
    version=Column(Integer,nullable=False)
    user_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    kind=Column(String(32),nullable=False)
    due_at=Column(DateTime,nullable=False,index=True)
    state=Column(String(16),nullable=False,default='PENDING')
    __table_args__=(UniqueConstraint('opportunity_id','version','user_id','kind','due_at',name='uq_pipeline_reminder'),Index('ix_pipeline_due','state','due_at'))

class SalesPipelineControl(Base):
    __tablename__='sales_pipeline_control'
    id=Column(Integer,primary_key=True)
    data=Column(JSON,nullable=False,default=dict)
