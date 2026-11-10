"""Ad mappings wrap the existing saved question engine; no parallel execution state machine."""
from datetime import datetime
from sqlalchemy import Column,Integer,String,Boolean,DateTime,ForeignKey,JSON
from database import Base


class AdFlowControl(Base):
    __tablename__='ad_flow_control'
    id=Column(Integer,primary_key=True)
    enabled=Column(Boolean,nullable=False,default=False)
    version=Column(Integer,nullable=False,default=0)


class AdFlowRule(Base):
    __tablename__='ad_flow_rules'
    id=Column(Integer,primary_key=True)
    ad_id=Column(String(64),nullable=False,unique=True,index=True)
    name=Column(String(120),nullable=False)
    flow_id=Column(Integer,ForeignKey('customer_question_flows.id',ondelete='RESTRICT'),nullable=False)
    flow_version=Column(Integer,nullable=False)
    policy=Column(String(16),nullable=False,default='FIRST')
    cooldown_hours=Column(Integer,nullable=False,default=24)
    allow_assigned=Column(Boolean,nullable=False,default=False)
    enabled=Column(Boolean,nullable=False,default=True)
    deleted=Column(Boolean,nullable=False,default=False)
    version=Column(Integer,nullable=False,default=1)
    updated_by=Column(Integer,ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class AdFlowExecution(Base):
    __tablename__='ad_flow_executions'
    id=Column(Integer,primary_key=True)
    rule_id=Column(Integer,ForeignKey('ad_flow_rules.id',ondelete='RESTRICT'),nullable=False,index=True)
    rule_version=Column(Integer,nullable=False)
    message_id=Column(Integer,ForeignKey('whatsapp_conversation_messages.id',ondelete='RESTRICT'),nullable=False,unique=True)
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id',ondelete='RESTRICT'),nullable=False,index=True)
    run_id=Column(Integer,ForeignKey('customer_question_runs.id',ondelete='RESTRICT'),nullable=True,unique=True)
    status=Column(String(16),nullable=False)
    reason=Column(String(48),nullable=True)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class AdFlowAudit(Base):
    __tablename__='ad_flow_audits'
    id=Column(Integer,primary_key=True)
    actor_id=Column(Integer,ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    rule_id=Column(Integer,ForeignKey('ad_flow_rules.id',ondelete='RESTRICT'),nullable=True,index=True)
    action=Column(String(24),nullable=False)
    detail=Column(JSON,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
