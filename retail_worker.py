"""Existing in-process background-job convention; feature flag gates each cycle."""
import logging
from settings import settings
import retail_sync
import retail_labels

def worker(sessions,stop):
    while not stop.is_set():
        try:
            if settings.RETAIL_ENABLED and settings.SHOPIFY_SHOP:
                if retail_labels.dispatch_one(sessions):continue
                if retail_sync.dispatch_one(sessions):continue
        except Exception:
            logging.getLogger(__name__).error('Retail job unavailable; inspect job history; no external writes performed')
        stop.wait(3)
