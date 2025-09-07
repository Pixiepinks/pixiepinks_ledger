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
from models import Account, JournalEntry, JournalLine, User, Base
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
