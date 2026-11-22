"""Existing in-process background-job convention; feature flag gates each cycle."""
import logging
from settings import settings
import hardware_service
import pos_service
import purchasing_service
import inventory_service
import retail_sync
import retail_labels
import retail_barcode_sync

def worker(sessions,stop):
    while not stop.is_set():
        try:hardware_service.tick(sessions)
        except Exception:logging.getLogger(__name__).error('Hardware queue unavailable; financial workflow remains independent')
        try:
            sold=pos_service.dispatch_one(sessions) if settings.POS_ENABLED and settings.SHOPIFY_SHOP else False
            if settings.RETAIL_ENABLED and settings.SHOPIFY_SHOP:
                purchasing_service.prepare_one(sessions)
                inventoried=inventory_service.dispatch_one(sessions)
                checked=retail_barcode_sync.check_preview_one(sessions)
                dispatched=retail_barcode_sync.dispatch_one(sessions)
                labeled=retail_labels.dispatch_one(sessions)
                imported=retail_sync.dispatch_one(sessions)
                if sold or inventoried or checked or dispatched or labeled or imported:continue
            elif sold:continue
        except Exception:
            logging.getLogger(__name__).error('Retail job unavailable; inspect durable job history; write outcome must be reconciled')
        stop.wait(3)
