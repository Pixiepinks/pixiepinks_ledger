from datetime import date, datetime
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func, inspect
from sqlalchemy.orm import Session

from settings import settings
from database import SessionLocal, engine
from models import Account, JournalEntry, JournalLine, User, Base, Customer, Supplier, Item
from seed import init_db
from utils_auth import hash_password, verify_password

# ---------------------- App & Middleware ----------------------
app = FastAPI(title=settings.APP_NAME)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    same_site="lax",
    https_only=True,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ---------------------- DB Session ----------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------------------- Helpers ----------------------
LOGIN_PATH = "/login"

def _is_safe_next(next_url: str) -> bool:
    try:
        u = urlparse(next_url)
        return (not u.netloc) and next_url.startswith("/")
    except Exception:
        return False

def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    if uid:
        user = db.get(User, uid)
        if user:
            return user
        request.session.clear()
    raise HTTPException(
        status_code=303,
        headers={"Location": f"{LOGIN_PATH}?next={quote(request.url.path)}"}
    )

# ---------------------- Startup ----------------------
@app.on_event("startup")
def startup():
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        Base.metadata.create_all(bind=engine)
    else:
        Base.metadata.create_all(bind=engine)

    init_db()

    with SessionLocal() as db:
        if not db.query(User).filter_by(username="admin").first():
            db.add(User(username="admin", password_hash=hash_password("change-me")))
            db.commit()

# ---------------------- Auth Routes ----------------------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str | None = "/"):
    return templates.TemplateResponse("login.html", {"request": request, "next": next})

@app.post("/login")
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    if user and verify_password(password, user.password_hash):
        request.session["uid"] = user.id
        request.session["user"] = {"username": user.username}
        if not _is_safe_next(next):
            next = "/"
        return RedirectResponse(next, status_code=303)
    return RedirectResponse(f"{LOGIN_PATH}?error=Invalid&next={quote(next)}", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(LOGIN_PATH, status_code=303)

# ---------------------- Protected Pages ----------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    today = date.today()
    start_month = today.replace(day=1)

    revenue = (
        db.query(func.coalesce(func.sum(JournalLine.credit), 0))
        .join(Account).filter(Account.type == "INCOME")
        .join(JournalEntry).filter(JournalEntry.date >= start_month, JournalEntry.date <= today)
        .scalar() or 0
    )
    expenses = (
        db.query(func.coalesce(func.sum(JournalLine.debit), 0))
        .join(Account).filter(Account.type == "EXPENSE")
        .join(JournalEntry).filter(JournalEntry.date >= start_month, JournalEntry.date <= today)
        .scalar() or 0
    )
    profit = float(revenue) - float(expenses)

    cash_acc = db.query(Account).filter(Account.name.in_(["Cash on Hand", "Bank - Current Account"])).all()
    cash_balance = 0.0
    for acc in cash_acc:
        dr = db.query(func.coalesce(func.sum(JournalLine.debit), 0)).filter(JournalLine.account_id == acc.id).scalar() or 0
        cr = db.query(func.coalesce(func.sum(JournalLine.credit), 0)).filter(JournalLine.account_id == acc.id).scalar() or 0
        cash_balance += float(dr) - float(cr)

    return templates.TemplateResponse("dashboard.html", {
        "request": request, "currency": settings.CURRENCY,
        "revenue": revenue, "expenses": expenses, "profit": profit,
        "cash_balance": cash_balance
    })

@app.get("/accounts", response_class=HTMLResponse)
def list_accounts(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    accounts = db.query(Account).order_by(Account.code).all()
    return templates.TemplateResponse("accounts.html", {"request": request, "accounts": accounts})

@app.post("/accounts")
def create_account(
    code: str = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    subtype: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_db)
):
    acc = Account(
        code=code.strip(),
        name=name.strip(),
        type=type.strip().upper(),
        subtype=subtype.strip(),
        description=description.strip()
    )
    db.add(acc)
    db.commit()
    return RedirectResponse("/accounts", status_code=303)

# ---------------------- Customers ----------------------
@app.get("/customers", response_class=HTMLResponse)
def list_customers(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    customers = db.query(Customer).order_by(Customer.name).all()
    return templates.TemplateResponse("customers.html", {"request": request, "customers": customers})

@app.post("/customers")
def create_customer(name: str = Form(...), email: str = Form(""), phone: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_user)):
    c = Customer(name=name.strip(), email=email.strip(), phone=phone.strip())
    db.add(c)
    db.commit()
    return RedirectResponse("/customers", status_code=303)

@app.post("/customers/{cust_id}/delete")
def delete_customer(cust_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    c = db.get(Customer, cust_id)
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    db.delete(c)
    db.commit()
    return RedirectResponse("/customers", status_code=303)

# ---------------------- Suppliers ----------------------
@app.get("/suppliers", response_class=HTMLResponse)
def list_suppliers(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    suppliers = db.query(Supplier).order_by(Supplier.name).all()
    return templates.TemplateResponse("suppliers.html", {"request": request, "suppliers": suppliers})

@app.post("/suppliers")
def create_supplier(name: str = Form(...), email: str = Form(""), phone: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_user)):
    s = Supplier(name=name.strip(), email=email.strip(), phone=phone.strip())
    db.add(s)
    db.commit()
    return RedirectResponse("/suppliers", status_code=303)

@app.post("/suppliers/{sup_id}/delete")
def delete_supplier(sup_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    s = db.get(Supplier, sup_id)
    if not s:
        raise HTTPException(status_code=404, detail="Supplier not found")
    db.delete(s)
    db.commit()
    return RedirectResponse("/suppliers", status_code=303)

# ---------------------- Items ----------------------
@app.get("/items", response_class=HTMLResponse)
def list_items(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    items = db.query(Item).order_by(Item.name).all()
    return templates.TemplateResponse("items.html", {"request": request, "items": items})

@app.post("/items")
def create_item(name: str = Form(...), unit: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_user)):
    i = Item(name=name.strip(), unit=unit.strip())
    db.add(i)
    db.commit()
    return RedirectResponse("/items", status_code=303)

@app.post("/items/{item_id}/delete")
def delete_item(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    i = db.get(Item, item_id)
    if not i:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(i)
    db.commit()
    return RedirectResponse("/items", status_code=303)


# ---------------------- Entries ----------------------
@app.get("/entries", response_class=HTMLResponse)
def list_entries(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    entries = db.query(JournalEntry).order_by(JournalEntry.date.desc(), JournalEntry.id.desc()).limit(200).all()
    accounts = db.query(Account).order_by(Account.code).all()
    customers = db.query(Customer).order_by(Customer.name).all()
    suppliers = db.query(Supplier).order_by(Supplier.name).all()
    items = db.query(Item).order_by(Item.name).all()
    return templates.TemplateResponse(
        "entries.html",
        {
            "request": request,
            "entries": entries,
            "accounts": accounts,
            "customers": customers,
            "suppliers": suppliers,
            "items": items,
            "currency": settings.CURRENCY,
        },
    )

@app.post("/entries")
def create_entry(
    date_str: str = Form(...),
    memo: str = Form(""),
    accounts: list[int] = Form(...),
    descriptions: list[str] = Form(...),
    debits: list[str] = Form(...),
    credits: list[str] = Form(...),
    party_types: list[str] = Form(...),
    party_ids: list[str] = Form(...),
    qtys: list[str] = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    entry = JournalEntry(date=dt, memo=memo)
    db.add(entry)
    db.flush()

    total_debit = 0.0
    total_credit = 0.0
    for a, d, dr, cr, pt, pid, q in zip(accounts, descriptions, debits, credits, party_types, party_ids, qtys):
        dr_amt = float(dr or 0)
        cr_amt = float(cr or 0)
        total_debit += dr_amt
        total_credit += cr_amt

        line = JournalLine(
            entry_id=entry.id,
            account_id=int(a),
            description=d.strip() if d else "",
            debit=dr_amt,
            credit=cr_amt,
            party_type=pt or None,
            party_id=int(pid) if pid else None,
            qty=float(q or 0)
        )
        db.add(line)

    if round(total_debit, 2) != round(total_credit, 2):
        db.rollback()
        return RedirectResponse("/entries?error=Not%20balanced", status_code=303)

    db.commit()
    return RedirectResponse("/entries", status_code=303)

@app.post("/entries/{entry_id}/delete")
@app.get("/entries/{entry_id}/delete", include_in_schema=False)
def delete_entry(entry_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    entry = db.get(JournalEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
    return RedirectResponse(url="/entries", status_code=status.HTTP_303_SEE_OTHER)

# ---------------------- Reports ----------------------
# (Trial Balance, Income Statement, Balance Sheet remain same as your version)
@app.get("/reports/trial-balance", response_class=HTMLResponse)
def trial_balance(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
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

    return templates.TemplateResponse(
        "trial_balance.html",
        {
            "request": request,
            "rows": rows,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "currency": settings.CURRENCY,
            "start": start,
            "end": end,
        },
    )

@app.get("/reports/income-statement", response_class=HTMLResponse)
def income_statement(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    from datetime import datetime as dt
    if not start or not end:
        return templates.TemplateResponse(
            "income_statement.html",
            {
                "request": request,
                "currency": settings.CURRENCY,
                "start": start,
                "end": end,
                "income": 0,
                "cogs": 0,
                "other_exp": 0,
                "gross_profit": 0,
                "net_profit": 0,
            },
        )

    start_dt = dt.strptime(start, "%Y-%m-%d").date()
    end_dt = dt.strptime(end, "%Y-%m-%d").date()

    income = (
        db.query(func.coalesce(func.sum(JournalLine.credit), 0))
        .join(Account).filter(Account.type == "INCOME")
        .join(JournalEntry)
        .filter(JournalEntry.date >= start_dt, JournalEntry.date <= end_dt)
        .scalar() or 0
    )
    cogs = (
        db.query(func.coalesce(func.sum(JournalLine.debit), 0))
        .join(Account).filter(Account.code == "5000")
        .join(JournalEntry)
        .filter(JournalEntry.date >= start_dt, JournalEntry.date <= end_dt)
        .scalar() or 0
    )
    other_exp = (
        db.query(func.coalesce(func.sum(JournalLine.debit), 0))
        .join(Account).filter(Account.type == "EXPENSE", Account.code != "5000")
        .join(JournalEntry)
        .filter(JournalEntry.date >= start_dt, JournalEntry.date <= end_dt)
        .scalar() or 0
    )

    gross_profit = float(income) - float(cogs)
    net_profit = gross_profit - float(other_exp)

    return templates.TemplateResponse(
        "income_statement.html",
        {
            "request": request,
            "currency": settings.CURRENCY,
            "start": start,
            "end": end,
            "income": income,
            "cogs": cogs,
            "other_exp": other_exp,
            "gross_profit": gross_profit,
            "net_profit": net_profit,
        },
    )


# ---------------------- Balance Sheet ----------------------
@app.get("/reports/balance-sheet", response_class=HTMLResponse)
def balance_sheet(request: Request, as_of: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_user)):
    from datetime import datetime as dt

    if not as_of:
        return templates.TemplateResponse("balance_sheet.html", {
            "request": request, "currency": settings.CURRENCY, "as_of": None,
            "assets_current": [], "assets_non_current": [],
            "liab_current": [], "liab_non_current": [],
            "equity_capital": [], "retained_earnings": 0,
            "assets_total": 0, "liab_total": 0,
            "equity_total": 0, "liab_equity_total": 0
        })

    as_of_dt = dt.strptime(as_of, "%Y-%m-%d").date()

    def account_balance(acc: Account):
        dr = db.query(func.coalesce(func.sum(JournalLine.debit), 0))\
            .filter(JournalLine.account_id == acc.id).join(JournalEntry).filter(JournalEntry.date <= as_of_dt).scalar() or 0
        cr = db.query(func.coalesce(func.sum(JournalLine.credit), 0))\
            .filter(JournalLine.account_id == acc.id).join(JournalEntry).filter(JournalEntry.date <= as_of_dt).scalar() or 0
        if acc.type in {"ASSET", "EXPENSE"}:
            return float(dr) - float(cr)
        return float(cr) - float(dr)

    accounts = db.query(Account).all()

    assets_current, assets_non_current = [], []
    liab_current, liab_non_current = [], []
    equity_capital = []
    retained_earnings = 0

    for acc in accounts:
        bal = account_balance(acc)
        if abs(bal) < 0.01:
            continue
        if acc.type == "ASSET":
            if acc.subtype == "CURRENT_ASSET":
                assets_current.append((acc.name, bal))
            elif acc.subtype == "NON_CURRENT_ASSET":
                assets_non_current.append((acc.name, bal))
        elif acc.type == "LIABILITY":
            if acc.subtype == "CURRENT_LIABILITY":
                liab_current.append((acc.name, bal))
            elif acc.subtype == "NON_CURRENT_LIABILITY":
                liab_non_current.append((acc.name, bal))
        elif acc.type == "EQUITY":
            if acc.subtype == "CAPITAL":
                equity_capital.append((acc.name, bal))
            elif acc.subtype == "RETAINED_EARNINGS":
                retained_earnings += bal
        elif acc.type == "INCOME":
            retained_earnings += bal
        elif acc.type == "EXPENSE":
            retained_earnings -= bal

    assets_total = sum(b for _, b in assets_current + assets_non_current)
    liab_total = sum(b for _, b in liab_current + liab_non_current)
    eq_cap_total = sum(b for _, b in equity_capital)

    equity_total = eq_cap_total + retained_earnings
    liab_equity_total = liab_total + equity_total

    return templates.TemplateResponse("balance_sheet.html", {
        "request": request, "currency": settings.CURRENCY, "as_of": as_of,
        "assets_current": assets_current, "assets_non_current": assets_non_current,
        "liab_current": liab_current, "liab_non_current": liab_non_current,
        "equity_capital": equity_capital, "retained_earnings": retained_earnings,
        "assets_total": assets_total, "liab_total": liab_total,
        "equity_total": equity_total, "liab_equity_total": liab_equity_total
    })

