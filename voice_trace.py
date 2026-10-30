"""Opt-in per-attempt evidence. No audio, phone, filename or token is recorded."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import logging
import re
from uuid import uuid4

# Uvicorn configures its own loggers, not the application root logger. Do not
# enable root INFO: other legacy routes can log customer data. This namespace
# emits only the explicitly selected evidence fields below.
logger=logging.getLogger("pixiepinks.voice_evidence")
logger.setLevel(logging.INFO)
logger.propagate=False
if not logger.handlers:
    handler=logging.StreamHandler()
    handler.setFormatter(logging.Formatter("VOICE_EVIDENCE %(levelname)s %(message)s"))
    logger.addHandler(handler)
INSTANCE=uuid4().hex
_active=ContextVar('voice_delivery_trace',default=None)


def graph_version(value):
    return value if isinstance(value,str) and re.fullmatch(r'v\d+\.\d+',value) else 'unrecognized'


def fingerprint(raw):
    return {'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw)}


def new(route):
    import build_info
    provenance=build_info.runtime()
    return {'trace_id':uuid4().hex,'route':route,'created_at':datetime.now(timezone.utc).isoformat(),
        'process_instance':INSTANCE,'runtime':provenance,'stages':{}}


@contextmanager
def capture(trace):
    token=_active.set(trace)
    try:yield
    finally:_active.reset(token)


def record(stage, values):
    trace=_active.get()
    if trace is not None:
        trace['stages'][stage]=deepcopy(values)
    logger.info('Voice evidence trace_id=%s stage=%s evidence=%s',trace.get('trace_id') if trace else None,stage,values)


def snapshot(trace):
    return deepcopy(trace)
