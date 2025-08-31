from sqlalchemy.orm import Session
from sqlalchemy import func, select
from models import JournalLine, Account
from typing import Dict

# Normal balance: debit for ASSET/EXPENSE, credit for LIABILITY/EQUITY/INCOME
DEBIT_NORMAL = {"ASSET", "EXPENSE"}
CREDIT_NORMAL = {"LIABILITY", "EQUITY", "INCOME"}

def account_balance(session: Session, account_id: int, start=None, end=None):
    q = select(
        func.coalesce(func.sum(JournalLine.debit), 0).label("debit"),
        func.coalesce(func.sum(JournalLine.credit), 0).label("credit")
    ).where(JournalLine.account_id == account_id)
    if start:
        q = q.join(JournalLine.entry).where(JournalLine.entry.has(date__gte=start))
    if end:
        q = q.join(JournalLine.entry).where(JournalLine.entry.has(date__lte=end))
    row = session.execute(q).one()
    return float(row.debit or 0) - float(row.credit or 0)

def account_balance_normal(session: Session, account: Account, start=None, end=None):
    q = session.query(JournalLine).join(Account, JournalLine.account_id == Account.id)
    if start or end:
        from models import JournalEntry
        q = q.join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
    if start:
        q = q.filter(JournalEntry.date >= start)
    if end:
        q = q.filter(JournalEntry.date <= end)

    debit = session.query(func.coalesce(func.sum(JournalLine.debit), 0)).filter(JournalLine.account_id == account.id)
    credit = session.query(func.coalesce(func.sum(JournalLine.credit), 0)).filter(JournalLine.account_id == account.id)

    if start or end:
        # recompute with date filter
        debit = q.filter(JournalLine.account_id == account.id).with_entities(func.coalesce(func.sum(JournalLine.debit), 0))
        credit = q.filter(JournalLine.account_id == account.id).with_entities(func.coalesce(func.sum(JournalLine.credit), 0))

    dr = float(debit.scalar() or 0)
    cr = float(credit.scalar() or 0)
    bal = dr - cr
    if account.type in DEBIT_NORMAL:
        return {"debit": max(bal, 0.0), "credit": max(-bal, 0.0), "normal_balance": bal}
    else:
        # credit-normal -> invert sign for presentation
        bal = -bal
        return {"debit": max(-bal, 0.0), "credit": max(bal, 0.0), "normal_balance": bal}