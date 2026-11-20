"""Downstream hardware intent and audit; never a financial or stock ledger."""
from datetime import datetime
from sqlalchemy import Column,String,Integer,DateTime,JSON,ForeignKey,LargeBinary
from database import Base

class PrintDevice(Base):
    __tablename__='print_devices'
    id=Column(String(36),primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    register_id=Column(Integer,ForeignKey('pos_registers.id'),nullable=False)
    active_register_id=Column(Integer,ForeignKey('pos_registers.id'),unique=True)
    location_id=Column(String(100),nullable=False)
    name=Column(String(100),nullable=False)
    status=Column(String(20),nullable=False,default='PAIRING')
    version=Column(Integer,nullable=False,default=1)
    config=Column(JSON,nullable=False)
    token_hash=Column(String(64))
    pair_hash=Column(String(64))
    pair_expires=Column(DateTime)
    enabled_at=Column(DateTime)
    last_seen=Column(DateTime)
    observation=Column(JSON)
    media=Column(String(20),nullable=False,default='UNKNOWN')
    mode_request=Column(String(20))
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class HardwareJob(Base):
    __tablename__='hardware_jobs'
    id=Column(String(36),primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    device_id=Column(String(36),ForeignKey('print_devices.id'),nullable=False,index=True)
    register_id=Column(Integer,ForeignKey('pos_registers.id'),nullable=False)
    location_id=Column(String(100),nullable=False)
    device_version=Column(Integer,nullable=False)
    business_key=Column(String(150),nullable=False,unique=True)
    kind=Column(String(20),nullable=False)
    queue=Column(String(150),nullable=False)
    media=Column(String(20),nullable=False)
    actor_id=Column(Integer,ForeignKey('users.id'))
    session_id=Column(String(36),ForeignKey('pos_sessions.id'))
    checkout_id=Column(String(36),ForeignKey('pos_checkouts.id'))
    label_id=Column(Integer,ForeignKey('retail_print_jobs.id'))
    parent_id=Column(String(36),ForeignKey('hardware_jobs.id'))
    detail=Column(JSON,nullable=False)
    pdf=Column(LargeBinary)
    sha256=Column(String(64),nullable=False)
    size=Column(Integer,nullable=False)
    status=Column(String(30),nullable=False,default='QUEUED',index=True)
    claim_hash=Column(String(64))
    attempts=Column(Integer,nullable=False,default=0)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    expires_at=Column(DateTime,nullable=False)
    permitted_at=Column(DateTime)
    delivered_at=Column(DateTime)
    finished_at=Column(DateTime)
    result=Column(JSON)

class HardwareEvent(Base):
    __tablename__='hardware_events'
    id=Column(Integer,primary_key=True)
    shop=Column(String(100),nullable=False,index=True)
    device_id=Column(String(36),ForeignKey('print_devices.id'),nullable=False)
    job_id=Column(String(36),ForeignKey('hardware_jobs.id'))
    actor_id=Column(Integer,ForeignKey('users.id'))
    action=Column(String(40),nullable=False)
    detail=Column(JSON,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)

class HardwareRate(Base):
    __tablename__='hardware_rates'
    key=Column(String(64),primary_key=True)
    bucket=Column(Integer,nullable=False)
    count=Column(Integer,nullable=False)
