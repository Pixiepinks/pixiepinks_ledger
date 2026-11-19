"""Existing in-process background-job convention; feature flag gates each cycle."""
import logging
from settings import settings
import retail_sync
import retail_labels
import retail_barcode_sync

def worker(sessions,stop):
    while not stop.is_set():
        try:
            if settings.RETAIL_ENABLED and settings.SHOPIFY_SHOP:
                checked=retail_barcode_sync.check_preview_one(sessions)
                dispatched=retail_barcode_sync.dispatch_one(sessions)
                labeled=retail_labels.dispatch_one(sessions)
                imported=retail_sync.dispatch_one(sessions)
                if checked or dispatched or labeled or imported:continue
        except Exception:
            logging.getLogger(__name__).error('Retail job unavailable; inspect durable job history; write outcome must be reconciled')
        stop.wait(3)
