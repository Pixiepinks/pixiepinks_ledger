from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey, Numeric, Text, Float, Enum
from sqlalchemy.orm import relationship, Mapped, mapped_column
from datetime import datetime
from database import Base
import enum

# ----------------------
# Party Type Enum
# ----------------------
class PartyType(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    ITEM = "ITEM"

# ----------------------
# Accounts & Journal
# ----------------------
class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # ASSET, LIABILITY, EQUITY, INCOME, EXPENSE
    subtype = Column(String, nullable=True)  # Current Asset, Non-Current Asset, etc.
    description = Column(String, nullable=True)

    lines = relationship("JournalLine", back_populates="account", cascade="all, delete-orphan")

class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    date: Mapped[datetime] = mapped_column(Date, index=True)
    memo: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    lines = relationship("JournalLine", back_populates="entry", cascade="all, delete-orphan")

class JournalLine(Base):
    __tablename__ = "journal_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(String(255), default="")
    debit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    credit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    # 🔹 New fields for Hybrid Sub-ledgers
    party_type = Column(Enum(PartyType), nullable=True)   # CUSTOMER / SUPPLIER / ITEM
    party_id = Column(Integer, nullable=True)             # Refers to Customer.id / Supplier.id / Item.id
    qty = Column(Float, default=0.0)                      # For inventory qty tracking

    entry = relationship("JournalEntry", back_populates="lines")
    account = relationship("Account", back_populates="lines")

# ----------------------
# Master Data Tables
# ----------------------
class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)

class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    sku = Column(String, nullable=False, unique=True, index=True)
    unit = Column(String, nullable=True)   # e.g., pcs, kg, box

# ----------------------
# Users
# ----------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
