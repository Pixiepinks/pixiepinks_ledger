from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import date, datetime, timedelta
from settings import settings
from database import SessionLocal
from models import Account, JournalEntry, JournalLine
from schemas import AccountCreate, JournalEntryIn
from seed import init_db
from sqlalchemy import func

app = FastAPI(title=settings.APP_NAME)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.on_event("startup")
def startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    # KPIs: Revenue MTD, Expenses MTD, Profit MTD, Cash Balance
    today = date.today()
    start_month = today.replace(day=1)
    # totals
    revenue = db.query(func.coalesce(func.sum(JournalLine.credit), 0)).join(Account).filter(Account.type == "INCOME").join(JournalEntry).filter(JournalEntry.date >= start_month, JournalEntry.date <= today).scalar() or 0
    expenses = db.query(func.coalesce(func.sum(JournalLine.debit), 0)).join(Account).filter(Account.type == "EXPENSE").join(JournalEntry).filter(JournalEntry.date >= start_month, JournalEntry.date <= today).scalar() or 0
    profit = float(revenue) - float(expenses)
    cash_acc = db.query(Account).filter(Account.name.in_(["Cash on Hand", "Bank - Current Account"])).all()
    cash_balance = 0.0
    for acc in cash_acc:
        dr = db.query(func.coalesce(func.sum(JournalLine.debit), 0)).filter(JournalLine.account_id == acc.id).scalar() or 0
        cr = db.query(func.coalesce(func.sum(JournalLine.credit), 0)).filter(JournalLine.account_id == acc.id).scalar() or 0
        # asset -> debit-normal
        cash_balance += float(dr) - float(cr)
    return templates.TemplateResponse("dashboard.html", {"request": request, "currency": settings.CURRENCY, "revenue": revenue, "expenses": expenses, "profit": profit, "cash_balance": cash_balance})

@app.get("/accounts", response_class=HTMLResponse)
def list_accounts(request: Request, db: Session = Depends(get_db)):
    accounts = db.query(Account).order_by(Account.code).all()
    return templates.TemplateResponse("accounts.html", {"request": request, "accounts": accounts})

@app.post("/accounts")
def create_account(code: str = Form(...), name: str = Form(...), type: str = Form(...), description: str = Form(""), db: Session = Depends(get_db)):
    acc = Account(code=code.strip(), name=name.strip(), type=type.strip().upper(), description=description.strip())
    db.add(acc)
    db.commit()
    return RedirectResponse("/accounts", status_code=303)

@app.get("/entries", response_class=HTMLResponse)
def list_entries(request: Request, db: Session = Depends(get_db)):
    entries = db.query(JournalEntry).order_by(JournalEntry.date.desc(), JournalEntry.id.desc()).limit(200).all()
    accounts = db.query(Account).order_by(Account.code).all()
    return templates.TemplateResponse("entries.html", {"request": request, "entries": entries, "accounts": accounts, "currency": settings.CURRENCY})

@app.post("/entries")
def create_entry(date_str: str = Form(...), memo: str = Form(""), accounts: list[int] = Form(...), descriptions: list[str] = Form(...), debits: list[str] = Form(...), credits: list[str] = Form(...), db: Session = Depends(get_db)):
    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    entry = JournalEntry(date=dt, memo=memo)
    db.add(entry)
    db.flush()
    total_debit = 0.0
    total_credit = 0.0
    for a, d, dr, cr in zip(accounts, descriptions, debits, credits):
        dr_amt = float(dr or 0)
        cr_amt = float(cr or 0)
        total_debit += dr_amt
        total_credit += cr_amt
        line = JournalLine(entry_id=entry.id, account_id=int(a), description=d, debit=dr_amt, credit=cr_amt)
        db.add(line)
    if round(total_debit, 2) != round(total_credit, 2):
        db.rollback()
        return RedirectResponse("/entries?error=Not%20balanced", status_code=303)
    db.commit()
    return RedirectResponse("/entries", status_code=303)
from fastapi import HTTPException

from fastapi.responses import RedirectResponse
from fastapi import HTTPException, status

@app.post("/entries/{entry_id}/delete")
@app.get("/entries/{entry_id}/delete", include_in_schema=False)  # fallback if browser hits it via GET
def delete_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.get(JournalEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)  # lines removed via cascade
    db.commit()
    return RedirectResponse(url="/entries", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/reports/trial-balance", response_class=HTMLResponse)
def trial_balance(request: Request, start: str | None = None, end: str | None = None, db: Session = Depends(get_db)):
    from datetime import datetime as dt
    start_dt = dt.strptime(start, "%Y-%m-%d").date() if start else None
    end_dt = dt.strptime(end, "%Y-%m-%d").date() if end else None
    accounts = db.query(Account).order_by(Account.code).all()
    rows = []
    total_debit = 0.0
    total_credit = 0.0
    for acc in accounts:
        dr = db.query(func.coalesce(func.sum(JournalLine.debit), 0)).join(JournalEntry).filter(JournalLine.account_id == acc.id)
        cr = db.query(func.coalesce(func.sum(JournalLine.credit), 0)).join(JournalEntry).filter(JournalLine.account_id == acc.id)
        if start_dt:
            dr = dr.filter(JournalEntry.date >= start_dt)
            cr = cr.filter(JournalEntry.date >= start_dt)
        if end_dt:
            dr = dr.filter(JournalEntry.date <= end_dt)
            cr = cr.filter(JournalEntry.date <= end_dt)
        dr_amt = float(dr.scalar() or 0)
        cr_amt = float(cr.scalar() or 0)
        bal = dr_amt - cr_amt
        debit = bal if bal > 0 else 0.0
        credit = -bal if bal < 0 else 0.0
        total_debit += debit
        total_credit += credit
        rows.append({"code": acc.code, "name": acc.name, "debit": debit, "credit": credit})
    return templates.TemplateResponse("trial_balance.html", {"request": request, "rows": rows, "total_debit": total_debit, "total_credit": total_credit, "currency": settings.CURRENCY, "start": start, "end": end})

@app.get("/reports/income-statement", response_class=HTMLResponse)
def income_statement(request: Request, start: str | None = None, end: str | None = None, db: Session = Depends(get_db)):
    from datetime import datetime as dt
    if not start or not end:
        return templates.TemplateResponse(
            "income_statement.html",
            {"request": request, "currency": settings.CURRENCY,
             "start": start, "end": end,
             "income": 0, "cogs": 0, "other_exp": 0,
             "gross_profit": 0, "net_profit": 0}
        )

    start_dt = dt.strptime(start, "%Y-%m-%d").date()
    end_dt = dt.strptime(end, "%Y-%m-%d").date()

    income = db.query(func.coalesce(func.sum(JournalLine.credit), 0))\
        .join(Account).filter(Account.type == "INCOME")\
        .join(JournalEntry).filter(JournalEntry.date >= start_dt, JournalEntry.date <= end_dt).scalar() or 0
    cogs = db.query(func.coalesce(func.sum(JournalLine.debit), 0))\
        .join(Account).filter(Account.code == "5000")\
        .join(JournalEntry).filter(JournalEntry.date >= start_dt, JournalEntry.date <= end_dt).scalar() or 0
    other_exp = db.query(func.coalesce(func.sum(JournalLine.debit), 0))\
        .join(Account).filter(Account.type == "EXPENSE", Account.code != "5000")\
        .join(JournalEntry).filter(JournalEntry.date >= start_dt, JournalEntry.date <= end_dt).scalar() or 0

    gross_profit = float(income) - float(cogs)
    net_profit = gross_profit - float(other_exp)

    return templates.TemplateResponse(
        "income_statement.html",
        {"request": request, "currency": settings.CURRENCY,
         "start": start, "end": end,
         "income": income, "cogs": cogs, "other_exp": other_exp,
         "gross_profit": gross_profit, "net_profit": net_profit}
    )


@app.get("/reports/balance-sheet", response_class=HTMLResponse)
def balance_sheet(request: Request, as_of: str | None = None, db: Session = Depends(get_db)):
    from datetime import datetime as dt

    # If no date yet, just render the form (zeros)
    if not as_of:
        return templates.TemplateResponse(
            "balance_sheet.html",
            {"request": request, "currency": settings.CURRENCY,
             "as_of": None, "assets": 0, "liabilities": 0, "equity": 0}
        )

    as_of_dt = dt.strptime(as_of, "%Y-%m-%d").date()

    accounts = db.query(Account).order_by(Account.code).all()

    def sum_type(acc_type: str):
        dr = db.query(func.coalesce(func.sum(JournalLine.debit), 0))\
               .join(Account).filter(Account.type == acc_type)\
               .join(JournalEntry).filter(JournalEntry.date <= as_of_dt).scalar() or 0
        cr = db.query(func.coalesce(func.sum(JournalLine.credit), 0))\
               .join(Account).filter(Account.type == acc_type)\
               .join(JournalEntry).filter(JournalEntry.date <= as_of_dt).scalar() or 0
        # debit-normal for ASSET/EXPENSE; credit-normal for LIABILITY/EQUITY/INCOME
        return float(dr) - float(cr) if acc_type in {"ASSET", "EXPENSE"} else float(cr) - float(dr)

    assets = sum_type("ASSET")
    liabilities = sum_type("LIABILITY")
    equity = sum_type("EQUITY")
    retained_from_income = sum_type("INCOME") - sum_type("EXPENSE")  # accumulated P&L
    total_equity = equity + retained_from_income

    return templates.TemplateResponse(
        "balance_sheet.html",
        {"request": request, "currency": settings.CURRENCY,
         "as_of": as_of, "assets": assets, "liabilities": liabilities, "equity": total_equity}
    )
