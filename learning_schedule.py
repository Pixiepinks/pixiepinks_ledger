"""Colombo schedule interpreted in UTC; no in-process scheduler."""
from datetime import datetime,timedelta,time,timezone
from zoneinfo import ZoneInfo

ZONE=ZoneInfo('Asia/Colombo')


def day_start(now=None):
    now=(now or datetime.utcnow()).replace(tzinfo=timezone.utc).astimezone(ZONE)
    return datetime.combine(now.date(),time(),ZONE).astimezone(timezone.utc).replace(tzinfo=None)


def due_slots(config,now=None):
    current=(now or datetime.utcnow()).replace(tzinfo=timezone.utc).astimezone(ZONE)
    hour,minute=map(int,config['schedule'].split(':'))
    return [(datetime.combine(current.date()-timedelta(days=offset),time(hour,minute),ZONE).astimezone(timezone.utc).replace(tzinfo=None),
             str(current.date()-timedelta(days=offset))) for offset in reversed(range(config['catchup_days']))
            if datetime.combine(current.date()-timedelta(days=offset),time(hour,minute),ZONE)<=current]


def next_run(config,now=None):
    current=(now or datetime.utcnow()).replace(tzinfo=timezone.utc).astimezone(ZONE)
    hour,minute=map(int,config['schedule'].split(':'));next_time=datetime.combine(current.date(),time(hour,minute),ZONE)
    if next_time<=current:next_time+=timedelta(days=1)
    return next_time.isoformat()
