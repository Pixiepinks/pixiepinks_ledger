"""General-ledger profit and loss totals, including POS contra-revenue.

No new journal entries are created. Income accounts have normal credit balances;
sales discounts (4010) and sales returns (4020) have debit balances. This report
uses net movements, not one-sided totals, so correcting/reversing entries count.
"""
from decimal import Decimal

from sqlalchemy import func

from models import Account, JournalEntry, JournalLine

DISCOUNT_CODE = "4010"
RETURN_CODE = "4020"
OTHER_INCOME_CODES = frozenset({"4100"})
COGS_CODE = "5000"


def income_statement_totals(db, start_dt, end_dt):
    """Summarize journal movements in an inclusive date range using Decimal.

    All INCOME accounts other than the named contra-revenue and other-income
    accounts count toward gross sales. Other income (currently account 4100)
    is shown separately below gross profit. No stock valuation or COGS is
    inferred; only posted journal lines are included.
    """
    totals = {
        "gross_sales": Decimal("0.00"),
        "sales_discounts": Decimal("0.00"),
        "sales_returns": Decimal("0.00"),
        "other_income": Decimal("0.00"),
        "cogs": Decimal("0.00"),
        "other_exp": Decimal("0.00"),
    }
    rows = (
        db.query(
            Account.code,
            Account.type,
            func.coalesce(func.sum(JournalLine.debit), 0).label("debit"),
            func.coalesce(func.sum(JournalLine.credit), 0).label("credit"),
        )
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .filter(JournalEntry.date >= start_dt, JournalEntry.date <= end_dt)
        .group_by(Account.id, Account.code, Account.type)
        .all()
    )

    for row in rows:
        debit = Decimal(str(row.debit or 0))
        credit = Decimal(str(row.credit or 0))
        if row.type == "INCOME":
            if row.code == DISCOUNT_CODE:
                totals["sales_discounts"] += debit - credit
            elif row.code == RETURN_CODE:
                totals["sales_returns"] += debit - credit
            elif row.code in OTHER_INCOME_CODES:
                totals["other_income"] += credit - debit
            else:
                totals["gross_sales"] += credit - debit
        elif row.type == "EXPENSE":
            if row.code == COGS_CODE:
                totals["cogs"] += debit - credit
            else:
                totals["other_exp"] += debit - credit

    totals["net_sales"] = (
        totals["gross_sales"] - totals["sales_discounts"] - totals["sales_returns"]
    )
    totals["gross_profit"] = totals["net_sales"] - totals["cogs"]
    totals["total_income"] = totals["net_sales"] + totals["other_income"]
    totals["total_expenses"] = totals["cogs"] + totals["other_exp"]
    totals["net_profit"] = totals["gross_profit"] + totals["other_income"] - totals["other_exp"]
    return totals
