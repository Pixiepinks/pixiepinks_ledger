"""Display-only LKR and Sri Lankan time formatting; no stored values changed."""
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
from zoneinfo import ZoneInfo

def local_datetime(value):
    if not value:return '—'
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(ZoneInfo('Asia/Colombo')).strftime('%d %b %Y, %I:%M %p').replace(', 0',', ')
    except (ValueError,TypeError,AttributeError):return '—'

def lkr(value):
    try:
        amount=Decimal(str(value))
        return 'Rs. '+format(amount,',.2f') if amount.is_finite() else '—'
    except (ValueError,TypeError,InvalidOperation):return '—'
