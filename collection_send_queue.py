"""Durable staff-authorized collection queue; independent of browser lifetime."""
import logging
import secrets
from datetime import datetime, timedelta
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, CheckConstraint, Index, or_
from sqlalchemy.orm import aliased
from fastapi import HTTPException
from database import Base


class CollectionSendJob(Base):
    __tablename__ = 'collection_send_jobs'
    batch_id = Column(Integer, ForeignKey('whatsapp_manual_sends.id'), primary_key=True)
    conversation_id = Column(Integer, ForeignKey('whatsapp_conversations.id'), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    status = Column(String(16), nullable=False, default='QUEUED', index=True)
    claim_token = Column(String(64))
    lease_until = Column(DateTime)
    next_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    reason = Column(String(200))
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (CheckConstraint("status IN ('QUEUED','RUNNING','PAUSING','PAUSED','BLOCKED','COMPLETE','CANCELED')", name='ck_collection_job_status'),
                      Index('ix_collection_queue_due', 'status', 'next_at'),)


SEND_ONE = None
log = logging.getLogger(__name__)


def earlier(db, job):
    return db.query(CollectionSendJob.batch_id).filter(CollectionSendJob.conversation_id == job.conversation_id,
        CollectionSendJob.batch_id < job.batch_id, CollectionSendJob.status.notin_(['COMPLETE','CANCELED'])).first() is not None


def allowed(db, job):
    from models import User
    from conversation_access import require_owned
    from staff_access import allowed as section
    user = db.query(User).filter_by(id=job.actor_id).populate_existing().one_or_none()
    if not user or not user.is_active or user.role not in {'ADMIN','STAFF','SALES_MANAGER','SALES_AGENT'} or not section(user, 'whatsapp'):
        raise HTTPException(403, 'Sending staff account no longer has Inbox access')
    require_owned(db, user, job.conversation_id)
    return user


def pause(db, job, reason):
    job.status = 'PAUSED'; job.reason = reason; job.updated_at = datetime.utcnow()


def dispatch_one(sessions):
    from models import WhatsAppManualSend as Send
    from shopify_collection_routes import get_batch, children, item_key, finish
    if SEND_ONE is None:
        return False
    now = datetime.utcnow(); previous = aliased(CollectionSendJob)
    with sessions() as db:
        prior = db.query(previous.batch_id).filter(previous.conversation_id == CollectionSendJob.conversation_id,
            previous.batch_id < CollectionSendJob.batch_id, previous.status.notin_(['COMPLETE','CANCELED'])).exists()
        job = db.query(CollectionSendJob).filter(CollectionSendJob.status.in_(['QUEUED','RUNNING','PAUSING']),
            CollectionSendJob.next_at <= now, or_(CollectionSendJob.lease_until.is_(None), CollectionSendJob.lease_until <= now), ~prior).order_by(CollectionSendJob.next_at, CollectionSendJob.batch_id).first()
        if not job:
            return False
        token = secrets.token_hex(24)
        claimed = db.query(CollectionSendJob).filter(CollectionSendJob.batch_id == job.batch_id,
            CollectionSendJob.status == job.status, or_(CollectionSendJob.lease_until.is_(None), CollectionSendJob.lease_until <= now)).update(
            {'claim_token': token, 'lease_until': now + timedelta(minutes=5)}, synchronize_session=False)
        if not claimed:
            db.rollback(); return True
        bid, cid = job.batch_id, job.conversation_id
        db.commit()
    try:
        with sessions() as db:
            job = db.get(CollectionSendJob, bid)
            if job.claim_token != token: return True
            user = allowed(db, job)
            batch, data = get_batch(db, cid, bid, user)
            rows = children(db, batch, data)
            if any(r.status in {'UNKNOWN','PROCESSING','VERIFYING'} for r in rows.values()):
                job.status = 'BLOCKED'; job.reason = 'An interrupted or unconfirmed send requires review; it will not be retried.'
                job.lease_until = None; job.claim_token = None; db.commit(); return True
            # A confirmed child may have committed just before a process died.
            # Recover only the parent summary; never reset a confirmed child.
            if batch.status == 'PROCESSING':
                finish(db, batch, data)
            next_item = next((p for p in data['items'] if rows[item_key(batch, p['id'])].status == 'QUEUED'), None)
            if not next_item:
                job.status = 'COMPLETE'; job.lease_until = None; job.claim_token = None; db.commit(); return True
            if job.status == 'PAUSING':
                pause(db, job, 'Paused by staff'); job.lease_until = None; job.claim_token = None; db.commit(); return True
            started = db.query(CollectionSendJob).filter(CollectionSendJob.batch_id == bid,
                CollectionSendJob.claim_token == token, CollectionSendJob.status.in_(['QUEUED','RUNNING'])).update(
                    {'status': 'RUNNING'}, synchronize_session=False)
            if not started:
                db.refresh(job)
                if job.status == 'PAUSING': pause(db, job, 'Paused by staff')
                job.lease_until = None; job.claim_token = None; db.commit(); return True
            db.commit()
            SEND_ONE(cid, bid, next_item['id'], db, user, background_token=token)
        with sessions() as db:
            job = db.query(CollectionSendJob).filter_by(batch_id=bid).populate_existing().with_for_update().one()
            if job.claim_token != token:
                return True
            batch = db.get(Send, bid)
            if job.status in {'PAUSED','BLOCKED','CANCELED'}:
                pass
            elif batch.status in {'COMPLETE','PARTIAL'}:
                job.status = 'COMPLETE'
            elif batch.status == 'BLOCKED':
                job.status = 'BLOCKED'; job.reason = 'Delivery outcome requires review. Unconfirmed products are not retried.'
            elif job.status == 'PAUSING':
                pause(db, job, 'Paused by staff')
            else:
                job.status = 'QUEUED'
            job.next_at = datetime.utcnow() + timedelta(seconds=1.1)
            job.lease_until = None; job.claim_token = None; job.updated_at = datetime.utcnow(); db.commit()
    except HTTPException as error:
        with sessions() as db:
            job = db.get(CollectionSendJob, bid)
            if job and job.claim_token == token:
                if error.status_code in {403,404}:
                    job.status = 'CANCELED'; job.reason = 'Sending staff access was removed. Remaining products will not be sent.'
                else:
                    pause(db, job, 'Reply window changed. Review the conversation before resuming.' if error.status_code != 429 else 'Product verification busy; resume to try again.')
                job.lease_until = None; job.claim_token = None; db.commit()
    except Exception:
        # Leave the durable lease/claim intact: restart recovery never blindly resends.
        log.error('COLLECTION_QUEUE_ATTEMPT_FAILED')
    return True


def worker(sessions, stop):
    while not stop.is_set():
        try:
            worked = dispatch_one(sessions)
        except Exception:
            log.error('COLLECTION_QUEUE_WORKER_FAILED'); worked = False
        stop.wait(.5 if worked else 2)
