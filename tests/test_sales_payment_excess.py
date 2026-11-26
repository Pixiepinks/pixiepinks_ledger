"""Excess receipts never become sale value or reusable credit automatically."""
from decimal import Decimal
import hashlib
import json
import importlib
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from database import SessionLocal
from models import SalesPayment, SalesPaymentAuditEvent, JournalEntry
from sales_payment_service import record_payment_claim, decide_payment, PaymentValidationError, _payload_hash
from shopify_payment_sync_service import _ledger_state
from test_sales_payments_phase4b import setup_function, _order, _claim, _client, _csrf
from test_retail_postgres import pg


def excess(amount='23000', reference='EXCESS-1'):
    return dict(_claim(amount, reference=reference), retain_excess=True, excess_reason='Exchange-rate difference')


def verify(db, order, payment):
    return decide_payment(db, order.id, payment.id, approve=True, actor='payment-admin', idempotency_key='verify-excess')


def test_received_applied_excess_and_no_financial_posting(monkeypatch):
    from shopify_catalog_service import ShopifyCatalogClient
    def forbidden(*args, **kwargs):
        raise AssertionError('No Shopify request is permitted during claim verification')
    monkeypatch.setattr(ShopifyCatalogClient, 'graphql', forbidden)
    with SessionLocal() as db:
        order = _order(db, '22410')
        before = db.query(JournalEntry).count()
        payment, _ = record_payment_claim(db, order.id, excess(), 'payment-agent', 'excess')
        assert payment.actual_received == Decimal('23000')
        assert payment.amount == Decimal('22410') and payment.excess_amount == Decimal('590')
        assert order.amount_paid == 0
        verify(db, order, payment)
        assert order.amount_paid == order.grand_total == Decimal('22410')
        assert order.payment_status == 'PAID'
        assert _ledger_state(db, order)[0] == Decimal('22410')
        assert db.query(JournalEntry).count() == before
        assert payment.accounting_entry_id is None and order.shopify_order_id is None
        assert verify(db, order, payment)[1] is False
        assert db.query(SalesPaymentAuditEvent).count() == 2
        evidence=json.loads(db.query(SalesPaymentAuditEvent).filter_by(action='CLAIM_VERIFIED').one().reason)
        assert Decimal(evidence['received_amount']) == 23000 and evidence['excess_status']=='HELD_FOR_REVIEW'
        from loyalty_service import financial
        order.order_status='DELIVERED'
        qualified, error=financial(db, order)
        assert error is None and qualified['paid'] == qualified['net'] == Decimal('22410')
        db.rollback()


def test_excess_rejection_is_not_money_received():
    with SessionLocal() as db:
        order = _order(db)
        p, _ = record_payment_claim(db, order.id, excess('1200'), 'payment-agent', 'rejected-excess')
        decide_payment(db, order.id, p.id, approve=False, actor='payment-admin', reason='Bank receipt not found', idempotency_key='reject-excess')
        assert order.amount_paid == 0 and p.verification_status == 'REJECTED'
        assert p.excess_amount == 200


@pytest.mark.parametrize('changes', [dict(retain_excess=False), dict(retain_excess='true'), dict(excess_reason='')])
def test_explicit_boolean_and_reason_required(changes):
    with SessionLocal() as db:
        order = _order(db)
        with pytest.raises(PaymentValidationError):
            record_payment_claim(db, order.id, dict(excess('1200'), **changes), 'payment-agent', 'invalid')
        assert db.query(SalesPayment).count() == 0


def test_pending_reservations_and_duplicate_references_remain_protected():
    with SessionLocal() as db:
        order = _order(db)
        record_payment_claim(db, order.id, _claim('400'), 'payment-agent', 'partial')
        p, _ = record_payment_claim(db, order.id, excess('700'), 'payment-agent', 'remainder')
        assert p.amount == 600 and p.excess_amount == 100
        assert record_payment_claim(db, order.id, excess('700'), 'payment-agent', 'remainder')[1] is False
        with pytest.raises(PaymentValidationError, match='different payment'):
            record_payment_claim(db, order.id, excess('701'), 'payment-agent', 'remainder')
        with pytest.raises(PaymentValidationError, match='already been recorded'):
            record_payment_claim(db, order.id, excess('700'), 'payment-agent', 'duplicate')
        with pytest.raises(PaymentValidationError, match='No unreserved'):
            record_payment_claim(db, order.id, excess('10', 'OTHER'), 'payment-agent', 'another')


def test_pos_excess_stays_blocked_and_legacy_hash_unchanged():
    with SessionLocal() as db:
        order = _order(db); order.sales_channel='POS'
        with pytest.raises(PaymentValidationError):
            record_payment_claim(db, order.id, excess('1200'), 'payment-agent', 'pos', pos_authorized=True)
        db.rollback()
    payload = _claim()
    old = {k: payload.get(k) for k in ('payment_method','amount','payment_date','payment_reference','notes')}
    assert _payload_hash(payload) == hashlib.sha256(json.dumps(old, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()
    p = SalesPayment(amount=Decimal('400'))
    assert p.actual_received == 400 and p.excess_amount == 0


def test_authenticated_api_and_staff_ui(monkeypatch):
    import main
    monkeypatch.setattr(main.settings, 'SALES_ORDERS_ENABLED', True)
    monkeypatch.setattr(main.settings, 'SALES_PAYMENT_TRACKING_ENABLED', True)
    with SessionLocal() as db:
        oid = _order(db, '22410').id
    client = _client(); csrf = _csrf(client)
    url = f'/api/sales-orders/{oid}/payment-claims'
    assert client.post(url, json=excess(), headers={'Idempotency-Key':'ui'}).status_code == 403
    result = client.post(url, json=excess(), headers={'Idempotency-Key':'ui', 'X-CSRF-Token':csrf})
    assert result.status_code == 201, result.text
    payment = result.json()['payment']
    assert Decimal(payment['received_amount']) == 23000 and Decimal(payment['amount']) == 22410
    assert payment['excess_status'] == 'UNVERIFIED'
    page = client.get(f'/crm/sales-orders/{oid}')
    assert page.status_code == 200 and 'Actual amount received' in page.text and 'Excess reason' in page.text
    result = client.post(f"/api/sales-orders/{oid}/payments/{payment['id']}/verify", headers={'Idempotency-Key':'ui-verify','X-CSRF-Token':csrf})
    assert result.status_code == 200 and result.json()['payment']['excess_status'] == 'HELD_FOR_REVIEW'
    assert 'HELD FOR REVIEW' in client.get(f'/crm/sales-orders/{oid}').text


def migration(conn, name):
    spec=importlib.util.spec_from_file_location('excess_migration', 'alembic/versions/20261126_60_payment_received.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(m, name)()


def test_postgres_migration_and_guarded_rollback(pg):
    engine, sessions, _ = pg
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE sales_payments DROP COLUMN received_amount'))
        conn.execute(text('ALTER TABLE sales_payments DROP COLUMN excess_reason'))
        migration(conn, 'upgrade'); migration(conn, 'upgrade')
        migration(conn, 'downgrade'); migration(conn, 'upgrade')
    with sessions() as db:
        order=_order(db); p,_=record_payment_claim(db,order.id,excess('1200'),'test','pg-excess')
        oid=order.id
    with engine.begin() as conn:
        with pytest.raises(RuntimeError, match='Retained excess'):
            migration(conn, 'downgrade')
    with sessions() as db:
        assert db.query(SalesPayment).filter_by(sales_order_id=oid).one().actual_received == 1200


def test_postgres_concurrent_excess_claim_reserves_balance_once(pg):
    _, sessions, _ = pg
    with sessions() as db: oid=_order(db).id
    def submit(i):
        with sessions() as db:
            try:
                return record_payment_claim(db,oid,excess('1200', f'PG-{i}'),'test',f'pg-{i}')[0].id
            except PaymentValidationError:
                db.rollback(); return None
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(submit, range(2)))
    assert sum(r is not None for r in results) == 1
    with sessions() as db:
        p=db.query(SalesPayment).filter_by(sales_order_id=oid).one()
        assert p.amount == 1000 and p.actual_received == 1200


def test_sqlite_additive_migration_preserves_legacy_amount_and_downgrade_guard():
    from sqlalchemy import create_engine
    engine=create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE sales_payments (id INTEGER PRIMARY KEY, amount NUMERIC(14,2) NOT NULL)'))
        conn.execute(text('INSERT INTO sales_payments (id,amount) VALUES (1,1000)'))
        migration(conn, 'upgrade'); migration(conn, 'upgrade')
        assert tuple(conn.execute(text('SELECT amount,received_amount FROM sales_payments')).one()) == (1000,None)
        migration(conn, 'downgrade'); migration(conn, 'upgrade')
        conn.execute(text("UPDATE sales_payments SET received_amount=1200,excess_reason='Exchange difference'"))
        with pytest.raises(RuntimeError, match='Retained excess'):
            migration(conn, 'downgrade')
        assert conn.execute(text('SELECT received_amount FROM sales_payments')).scalar() == 1200
    engine.dispose()
