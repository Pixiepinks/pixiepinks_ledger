"""Focused, disposable ledger reporting tests. No live CRM or Shopify calls."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from finance_reporting import income_statement_totals
from models import Account, JournalEntry, JournalLine


@pytest.fixture
def ledger():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[Account.__table__, JournalEntry.__table__, JournalLine.__table__]
    )
    session = sessionmaker(bind=engine)()
    try:
        accounts = {}
        for code, account_type in [
            ("1000", "ASSET"),
            ("1200", "ASSET"),
            ("4000", "INCOME"),
            ("4010", "INCOME"),
            ("4020", "INCOME"),
            ("4100", "INCOME"),
            ("5000", "EXPENSE"),
            ("5400", "EXPENSE"),
        ]:
            account = Account(code=code, name="Test " + code, type=account_type)
            session.add(account)
            session.flush()
            accounts[code] = account.id
        session.commit()
        yield session, accounts
    finally:
        session.close()
        engine.dispose()


def record(db, accounts, date_value, *movements):
    entry = JournalEntry(date=date_value, memo="Report test")
    db.add(entry)
    db.flush()
    for code, debit, credit in movements:
        db.add(
            JournalLine(
                entry_id=entry.id,
                account_id=accounts[code],
                debit=Decimal(debit),
                credit=Decimal(credit),
            )
        )
    db.commit()


def test_pos_discount_and_return_debits_reduce_net_sales(ledger):
    db, a = ledger
    d = date(2026, 11, 19)
    record(db, a, d, ("1000", "900.00", "0"), ("4010", "100.00", "0"), ("4000", "0", "1000.00"))
    record(db, a, d, ("4020", "200.00", "0"), ("1000", "0", "200.00"))
    record(db, a, d, ("1000", "50.00", "0"), ("4100", "0", "50.00"))
    record(db, a, d, ("5000", "350.00", "0"), ("1200", "0", "350.00"))
    record(db, a, d, ("5400", "70.00", "0"), ("1000", "0", "70.00"))
    # Correcting entries must reduce the original debit-balance contra-revenue.
    record(db, a, d, ("1000", "20.00", "0"), ("4010", "0", "20.00"))
    # Out-of-range journal entries must not affect this period.
    record(db, a, date(2026, 12, 1), ("1000", "9999.00", "0"), ("4000", "0", "9999.00"))

    got = income_statement_totals(db, d, d)
    expected = {
        "gross_sales": "1000.00",
        "sales_discounts": "80.00",
        "sales_returns": "200.00",
        "net_sales": "720.00",
        "cogs": "350.00",
        "gross_profit": "370.00",
        "other_income": "50.00",
        "other_exp": "70.00",
        "total_income": "770.00",
        "total_expenses": "420.00",
        "net_profit": "350.00",
    }
    assert got == {key: Decimal(value) for key, value in expected.items()}


def test_reversals_and_dates_are_netted_not_ignored(ledger):
    db, a = ledger
    day = date(2026, 11, 19)
    record(db, a, day, ("1000", "100.00", "0"), ("4000", "0", "100.00"))
    record(db, a, day, ("4000", "30.00", "0"), ("1000", "0", "30.00"))
    record(db, a, day, ("4020", "20.00", "0"), ("1000", "0", "20.00"))
    record(db, a, day, ("1000", "5.00", "0"), ("4020", "0", "5.00"))
    record(db, a, day, ("5400", "10.00", "0"), ("1000", "0", "10.00"))
    record(db, a, day, ("1000", "4.00", "0"), ("5400", "0", "4.00"))

    got = income_statement_totals(db, day, day)
    assert got["gross_sales"] == Decimal("70.00")
    assert got["sales_returns"] == Decimal("15.00")
    assert got["net_sales"] == Decimal("55.00")
    assert got["other_exp"] == Decimal("6.00")
    assert got["net_profit"] == Decimal("49.00")
    assert all(value == 0 for value in income_statement_totals(db, date(2026, 1, 1), date(2026, 1, 31)).values())


@pytest.mark.parametrize('template,field',[
    ('dashboard.html','profit'),
    ('income_statement.html','net_profit'),
])
def test_report_templates_preserve_decimal_cents(template,field):
    from pathlib import Path
    from jinja2 import ChoiceLoader,DictLoader,Environment,FileSystemLoader,select_autoescape
    env=Environment(loader=ChoiceLoader([
        DictLoader({'base.html':'{% block content %}{% endblock %}'}),
        FileSystemLoader(Path(__file__).parents[1]/'templates'),
    ]),autoescape=select_autoescape(['html']))
    context={key:Decimal('0.00') for key in [
        'gross_sales','sales_discounts','sales_returns','net_sales','cogs','gross_profit',
        'other_income','other_exp','net_profit','revenue','expenses','profit','cash_balance',
    ]}
    context.update(currency='LKR',start='2026-11-01',end='2026-11-19')
    context[field]=Decimal('100999999999998.99')
    rendered=env.get_template(template).render(**context)
    assert 'LKR 100999999999998.99' in rendered
    assert '100999999999998.98' not in rendered
