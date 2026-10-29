"""Database-backed automatic reply admission and cross-worker send barrier.

Context stores job identity, never a cached switch value. Every checkpoint reads
PostgreSQL afresh. Manual sends do not enter an automatic-job context.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from inspect import signature
import json
import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from models import GlobalAIReplyControl, BotAuditEvent, ProcessedWhatsAppMessage

logger = logging.getLogger(__name__)


class AutoReplyBlocked(Exception):
    pass


@dataclass(frozen=True)
class Job:
    sessions: object
    generation: int
    message_id: str


_job = ContextVar("automatic_reply_job", default=None)
_send_lock = ContextVar("automatic_reply_send_lock", default=False)


def in_automatic_job():
    return _job.get() is not None


def state(db):
    # Scalar column query avoids ORM identity-map or configuration-cache staleness.
    row = db.execute(select(GlobalAIReplyControl.enabled, GlobalAIReplyControl.generation,
        GlobalAIReplyControl.updated_by, GlobalAIReplyControl.updated_at).where(GlobalAIReplyControl.id == 1)).one()
    return {"enabled": row.enabled, "generation": row.generation,
            "updated_by": row.updated_by, "updated_at": row.updated_at}


def display_state(db):
    try:
        return {**state(db), "available": True}
    except SQLAlchemyError:
        db.rollback()
        return {"enabled": False, "generation": None, "available": False}


def admission(sessions):
    """Called once at receipt, before storing/enqueuing. Failure closes AI only."""
    try:
        with sessions() as db:
            value = state(db)
            return value["generation"] if value["enabled"] else None
    except SQLAlchemyError:
        logger.warning("Global AI setting unavailable; automatic admission denied")
        return None


def message_job(sessions, message_id, sender):
    try:
        with sessions() as db:
            record = db.execute(select(ProcessedWhatsAppMessage.ai_reply_generation).where(
                ProcessedWhatsAppMessage.message_id == message_id,
                ProcessedWhatsAppMessage.sender_phone == sender)).first()
            if record is None or record[0] is None:
                return None
            current = state(db)
            if current["enabled"] and current["generation"] == record[0]:
                return Job(sessions, record[0], message_id)
    except SQLAlchemyError:
        logger.warning("Global AI setting unavailable; queued reply denied")
    return None


def checkpoint(*, require_job=False):
    """Guard model calls within jobs; explicit staff simulations stay independent."""
    job = _job.get()
    if job is None:
        if require_job:
            raise AutoReplyBlocked()
        return
    try:
        with job.sessions() as db:
            current = state(db)
            if current["enabled"] and current["generation"] == job.generation:
                return
    except SQLAlchemyError:
        logger.warning("Global AI setting unavailable; running reply denied")
    raise AutoReplyBlocked()


def automatic_job(sessions_provider, *, message_index=1, sender_index=0, sessions_index=None):
    """Only a durably admitted receipt can start (or retry) an automatic job."""
    def decorate(fn):
        call_signature = signature(fn)
        parameters = tuple(call_signature.parameters)
        @wraps(fn)
        def run(*args, **kwargs):
            # Image/text and fallback helpers can call one another within one job.
            if _job.get() is not None:
                checkpoint()
                return fn(*args, **kwargs)
            bound = call_signature.bind(*args, **kwargs)
            bound.apply_defaults()
            message_id = bound.arguments[parameters[message_index]]
            sender = bound.arguments[parameters[sender_index]]
            sessions = bound.arguments[parameters[sessions_index]] if sessions_index is not None else sessions_provider()
            job = message_job(sessions, message_id, sender)
            if job is None:
                return None
            token = _job.set(job)
            try:
                checkpoint()
                return fn(*args, **kwargs)
            except AutoReplyBlocked:
                logger.info("Automatic reply suppressed by global control")
                return None
            finally:
                _job.reset(token)
        return run
    return decorate


@contextmanager
def send_boundary(*, require_job=False):
    """Hold a shared row lock through exactly one external send.

    OFF uses an exclusive lock on this same row and commits only after current
    sends release it. Shared locks permit multiple conversations concurrently.
    SQLite uses BEGIN IMMEDIATE for local development/test serialization.
    """
    job = _job.get()
    if job is None:
        if require_job:
            raise AutoReplyBlocked()
        yield  # Explicit human sends never depend on the global setting.
        return
    if _send_lock.get():
        yield  # Nested transport wrapper is already inside this send barrier.
        return
    entered = False
    try:
        with job.sessions() as db:
            if db.get_bind().dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = db.execute(select(GlobalAIReplyControl.enabled, GlobalAIReplyControl.generation)
                .where(GlobalAIReplyControl.id == 1).with_for_update(read=True)).one()
            if not row.enabled or row.generation != job.generation:
                raise AutoReplyBlocked()
            token = _send_lock.set(True)
            try:
                entered = True
                yield
            finally:
                _send_lock.reset(token)
                # Releasing the transaction lock never depends on history writes.
                db.rollback()
    except SQLAlchemyError as exc:
        if entered:
            # Once the provider boundary was entered, a failure may follow
            # external acceptance. Preserve uncertainty, never label it SKIPPED.
            raise
        logger.warning("Global AI send barrier unavailable; send denied")
        raise AutoReplyBlocked() from exc


def automatic_send(fn):
    @wraps(fn)
    def send(*args, **kwargs):
        with send_boundary(require_job=True):
            return fn(*args, **kwargs)
    return send


def transport_send(fn):
    @wraps(fn)
    def send(*args, **kwargs):
        with send_boundary():
            return fn(*args, **kwargs)
    return send


def change(db, *, enabled, expected_generation, actor):
    """Lock, compare-and-set and audit atomically. Caller commits/rolls back."""
    row = db.execute(select(GlobalAIReplyControl).where(GlobalAIReplyControl.id == 1)
        .with_for_update().execution_options(populate_existing=True)).scalar_one()
    if row.generation != expected_generation:
        raise ValueError("Global AI state changed. Reload Settings before trying again.")
    previous = row.enabled
    if previous == enabled:
        return False
    # UPDATE also serializes against SQLite's BEGIN IMMEDIATE send barrier.
    changed = db.query(GlobalAIReplyControl).filter_by(id=1, generation=expected_generation).update({
        "enabled": enabled, "generation": expected_generation + 1,
        "updated_by": actor, "updated_at": datetime.utcnow()}, synchronize_session=False)
    if changed != 1:
        raise ValueError("Global AI state changed. Reload Settings before trying again.")
    db.add(BotAuditEvent(action="GLOBAL_AI_AUTO_REPLIES", actor=actor,
        summary=json.dumps({"previous": previous, "new": enabled,
                            "generation": expected_generation + 1}, sort_keys=True)))
    db.flush()
    return True
