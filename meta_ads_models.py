"""Additive reporting tables; no credentials or customer/referral copies."""
from sqlalchemy import Column, String, Integer, Date, DateTime, Numeric, JSON, UniqueConstraint
from database import Base


class MetaAdsDaily(Base):
    __tablename__ = 'meta_ads_daily'
    __table_args__ = (UniqueConstraint('account_id', 'level', 'entity_id', 'report_date', name='uq_meta_ads_daily'),)
    id = Column(Integer, primary_key=True)
    account_id = Column(String(64), nullable=False, index=True)
    level = Column(String(16), nullable=False)
    entity_id = Column(String(64), nullable=False, index=True)
    report_date = Column(Date, nullable=False, index=True)
    campaign_id = Column(String(64))
    campaign_name = Column(String(500))
    adset_id = Column(String(64))
    adset_name = Column(String(500))
    ad_id = Column(String(64))
    ad_name = Column(String(500))
    currency = Column(String(8), nullable=False)
    spend = Column(Numeric(18, 4))
    impressions = Column(Integer)
    reach = Column(Integer)
    link_clicks = Column(Integer)
    messaging = Column(Numeric(18, 4))
    actions = Column(JSON)
    delivery_status = Column(String(64))
    source_start = Column(Date, nullable=False)
    source_stop = Column(Date, nullable=False)
    synced_at = Column(DateTime, nullable=False)


class MetaAdsSync(Base):
    __tablename__ = 'meta_ads_sync'
    account_id = Column(String(64), primary_key=True)
    currency = Column(String(8))
    timezone_name = Column(String(100))
    status = Column(String(32), nullable=False, default='NEVER')
    started_at = Column(DateTime)
    last_success_at = Column(DateTime)
    error_code = Column(String(64))
    diagnostics = Column(JSON)
    requested_at = Column(DateTime)
    date_from = Column(Date)
    date_to = Column(Date)
