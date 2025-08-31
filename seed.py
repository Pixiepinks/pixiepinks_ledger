from database import Base, engine, SessionLocal
from models import Account
from sqlalchemy.orm import Session

def seed_accounts(session: Session):
    # Basic Sri Lanka-friendly chart (generic)
    accounts = [
        ("1000", "Cash on Hand", "ASSET"),
        ("1010", "Bank - Current Account", "ASSET"),
        ("1100", "Accounts Receivable", "ASSET"),
        ("1200", "Inventory", "ASSET"),
        ("1500", "Prepaid Expenses", "ASSET"),
        ("2000", "Accounts Payable", "LIABILITY"),
        ("2100", "Taxes Payable (VAT/NBT)", "LIABILITY"),
        ("3000", "Owner's Equity", "EQUITY"),
        ("3100", "Retained Earnings", "EQUITY"),
        ("4000", "Sales Revenue", "INCOME"),
        ("4100", "Other Income", "INCOME"),
        ("5000", "Cost of Goods Sold", "EXPENSE"),
        ("5100", "Delivery & Courier Expense", "EXPENSE"),
        ("5200", "Advertising & Marketing", "EXPENSE"),
        ("5300", "Rent Expense", "EXPENSE"),
        ("5400", "Utilities Expense", "EXPENSE"),
        ("5500", "Bank Charges", "EXPENSE"),
    ]
    for code, name, typ in accounts:
        if not session.query(Account).filter_by(code=code).first():
            session.add(Account(code=code, name=name, type=typ, description=""))

def init_db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        seed_accounts(session)
        session.commit()
    finally:
        session.close()

if __name__ == "__main__":
    init_db()