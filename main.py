from fastapi import FastAPI, Request, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware  # NEW
from sqlalchemy.orm import Session
from datetime import date, datetime, timedelta
from settings import settings
from database import SessionLocal, engine  # engine for create_all
from models import Account, JournalEntry, JournalLine, User  # include User
from schemas import AccountCreate, JournalEntryIn
from seed import init_db
from sqlalchemy import func
from utils_auth import hash_password, verify_password  # NEW
from sqlalchemy import inspect  # NEW
from urllib.parse import quote, urlparse

app = FastAPI(title=settings.APP_NAME)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, same_site="lax", https_only=True,)  # NEW

LOGIN_PATH = "/login"

def _is_safe_next(next_url: str) -> bool:
    """Prevent open-redirects. Allow only same-site paths."""
    try:
        u = urlparse(next_url)
        return (not u.netloc) and next_url.startswith("/")
    except Exception:
        return False

def login_required(request: Request):
    """Dependency that either returns or raises a redirect to /login."""
    user = request.session.get("user")
    if user:
        return
    next_param = quote(request.url.path)
    # IMPORTANT: raise a redirect, don't return it
    raise HTTPException(status_code=307, headers={"Location": f"{LOGIN_PATH}?next={next_param}"})

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------- auth utilities ----------
def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    if not uid:
        # redirect to login (303) – FastAPI allows redirect via HTTPException headers
        raise HTTPException(status_code=303, headers={"Location": f"/login?next={request.url.path}"})
    user = db.get(User, uid)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user
# ------------------------------------

@app.on_event("startup")
def startup():
    # Create missing tables once
    from models import Base
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        Base.metadata.create_all(bind=engine)
    else:
        # still ensure future tables exist if you add more models later
        Base.metadata.create_all(bind=engine)

    # Seed your chart of accounts/etc.
    init_db()

    # Seed an admin if none exists
    with SessionLocal() as db:
        if not db.query(User).filter_by(username="admin").first():
            db.add(User(username="admin", password_hash=hash_password("change-me")))
            db.commit()

# ------------------------ AUTH ROUTES ------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str | None = "/"):
    return templates.TemplateResponse("login.html", {"request": request, "next": next})

<form method="post" action="/login">
  <input type="hidden" name="next" value="{{ next or '/' }}">
  <input name="username">
  <input type="password" name="password">
  <button>Sign in</button>
</form>
    
@app.post("/login")
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/")
):
    # TODO: replace with DB lookup later
    if username == "admin" and password == "change-me":
        request.session["user"] = {"username": username}
        if not _is_safe_next(next):
            next = "/"
        return RedirectResponse(next, status_code=303)
    return RedirectResponse(f"{LOGIN_PATH}?error=Invalid&next={quote(next)}", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(LOGIN_PATH, status_code=303)
    
# ---------------------- PROTECTED PAGES ----------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), _: None = Depends(login_required)):
    # ... your original code unchanged below ...
    today = date.today()
    start_month = today.replace(day=1)
    revenue = db.query(func.coalesce(func.sum(JournalLine.credit), 0)).join(Account).filter(Account.type == "INCOME").join(JournalEntry).filter(JournalEntry.date >= start_month, JournalEntry.date <= today).scalar() or 0
    expenses = db.query(func.coalesce(func.sum(JournalLine.debit), 0)).join(Account).filter(Account.type == "EXPENSE").join(JournalEntry).filter(JournalEntry.date >= start_month, JournalEntry.date <= today).scalar() or 0
    profit = float(revenue) - float(expenses)
    cash_acc = db.query(Account).filter(Account.name.in_(["Cash on Hand", "Bank - Current Account"])).all()
    cash_balance = 0.0
    for acc in cash_acc:
        dr = db.query(func.coalesce(func.sum(JournalLine.debit), 0)).filter(JournalLine.account_id == acc.id).scalar() or 0
        cr = db.query(func.coalesce(func.sum(JournalLine.credit), 0)).filter(JournalLine.account_id == acc.id).scalar() or 0
        cash_balance += float(dr) - float(cr)
    return templates.TemplateResponse("dashboard.html", {"request": request, "currency": settings.CURRENCY, "revenue": revenue, "expenses": expenses, "profit": profit, "cash_balance": cash_balance})

@app.get("/accounts", response_class=HTMLResponse)
def list_accounts(request: Request, db: Session = Depends(get_db), _: None = Depends(login_required)):
    accounts = db.query(Account).order_by(Account.code).all()
    return templates.TemplateResponse("accounts.html", {"request": request, "accounts": accounts})

@app.post("/accounts")
def create_account(code: str = Form(...), name: str = Form(...), type: str = Form(...), description: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_login)):
    acc = Account(code=code.strip(), name=name.strip(), type=type.strip().upper(), description=description.strip())
    db.add(acc)
    db.commit()
    return RedirectResponse("/accounts", status_code=303)

@app.get("/entries", response_class=HTMLResponse)
def list_entries(request: Request, db: Session = Depends(get_db), _: None = Depends(login_required)):
    entries = db.query(JournalEntry).order_by(JournalEntry.date.desc(), JournalEntry.id.desc()).limit(200).all()
    accounts = db.query(Account).order_by(Account.code).all()
    return templates.TemplateResponse("entries.html", {"request": request, "entries": entries, "accounts": accounts, "currency": settings.CURRENCY})

@app.post("/entries")
def create_entry(date_str: str = Form(...), memo: str = Form(""), accounts: list[int] = Form(...), descriptions: list[str] = Form(...), debits: list[str] = Form(...), credits: list[str] = Form(...), db: Session = Depends(get_db), user: User = Depends(require_login)):
    # ... unchanged body ...
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

@app.post("/entries/{entry_id}/delete")
@app.get("/entries/{entry_id}/delete", include_in_schema=False)
def delete_entry(entry_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)):
    entry = db.get(JournalEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
    return RedirectResponse(url="/entries", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/reports/trial-balance", response_class=HTMLResponse)
def trial_balance(request: Request, start: str | None = None, end: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_login)):
    # ... unchanged body ...
    # (keep your current code here)
    ...

@app.get("/reports/income-statement", response_class=HTMLResponse)
def income_statement(request: Request, start: str | None = None, end: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_login)):
    # ... unchanged body ...
    ...

@app.get("/reports/balance-sheet", response_class=HTMLResponse)
def balance_sheet(request: Request, as_of: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_login)):
    # ... unchanged body ...
    ...
