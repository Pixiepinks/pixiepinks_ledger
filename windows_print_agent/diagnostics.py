"""Best-effort stderr diagnostics: fixed labels only, never exception text/data."""
import json
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

STAGES = frozenset('''unclassified poll_cycle job_execution configuration_fetch
job_claim heartbeat outcome_ack journal_pending journal_identity journal_read
journal_intent journal_failed journal_submitted journal_unknown journal_ack
journal_open journal_crash_recovery journal_close attestation_load attestation_check
printer_status dispatch_lock permit_response permit_deadline progress_callback
progress_rpc progress_heartbeat pdf_fetch pdf_validation receipt80_validation
driver_submission pdf_backend_import pdf_open pdf_page driver_fingerprint
driver_mode driver_create_dc driver_wrap_dc driver_capabilities driver_geometry
driver_margins pdf_memory_budget pdf_render pdf_image_conversion gdi_start_doc
gdi_start_page gdi_draw gdi_end_page gdi_end_doc gdi_abort_doc gdi_release gdi_submission_result
pdf_cleanup'''.split())
CLASSES = frozenset('''Exception AgentError AttributeError ValueError TypeError
RuntimeError KeyError MemoryError NotImplementedError OSError PermissionError
FileNotFoundError TimeoutError ImportError ModuleNotFoundError HTTPStatusError
ConnectError ReadError WriteError CloseError ConnectTimeout ReadTimeout WriteTimeout
PoolTimeout RemoteProtocolError LocalProtocolError DecodingError UnsupportedProtocol
SSLError SSLCertVerificationError PdfiumError OperationalError DatabaseError
IntegrityError InterfaceError ProgrammingError Error error'''.split())
_scope = ContextVar('print_diagnostic_scope', default=None)


def _write(value):
    try:
        sys.stderr.write(json.dumps(value, separators=(',', ':')) + '\n')
        sys.stderr.flush()
    except Exception:
        pass  # Logging must never affect dispatch, journal durability or exceptions.


def failure(stage, exc):
    try:
        name = type(exc).__name__
        value = {'event': 'connector_error', 'stage': stage if stage in STAGES else 'unclassified',
                 'exception_class': name if name in CLASSES else 'OtherException'}
        for attribute, field in [('winerror', 'windows_error_code'), ('hresult', 'hresult'), ('errno', 'os_error_code')]:
            code = getattr(exc, attribute, None)
            if type(code) is int and -(2**31) <= code <= 2**32-1:
                value[field] = code
        if name == 'HTTPStatusError':
            code = getattr(getattr(exc, 'response', None), 'status_code', None)
            if type(code) is int and 100 <= code <= 599:
                value['http_status'] = code
        _write(value)
    except Exception:
        pass


@contextmanager
def step(stage):
    state = _scope.get()
    token = None
    if state is None:
        state = [None]
        token = _scope.set(state)
    try:
        yield
    except Exception as exc:
        # Retain only an identity integer, not an exception/traceback/secret payload.
        if state[0] != id(exc):
            failure(stage, exc)
            state[0] = id(exc)
        raise
    finally:
        if token is not None:
            _scope.reset(token)


def call(stage, function, *args, **kwargs):
    with step(stage):
        return function(*args, **kwargs)


def traced(stage):
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            with step(stage):
                return function(*args, **kwargs)
        return invoke
    return decorate


def recovered_intents(count):
    if type(count) is int and count > 0:
        _write({'event': 'connector_recovery', 'stage': 'journal_crash_recovery',
                'recovered_intent_count': count})
