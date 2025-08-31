from pydantic import BaseModel, Field
from datetime import date
from typing import List, Optional

class AccountCreate(BaseModel):
    code: str
    name: str
    type: str
    description: str = ""

class AccountOut(BaseModel):
    id: int
    code: str
    name: str
    type: str
    description: str

    class Config:
        from_attributes = True

class JournalLineIn(BaseModel):
    account_id: int
    description: str = ""
    debit: float = 0.0
    credit: float = 0.0

class JournalEntryIn(BaseModel):
    date: date
    memo: str = ""
    lines: List[JournalLineIn]

class DateRange(BaseModel):
    start: date
    end: date

class AsOf(BaseModel):
    as_of: date