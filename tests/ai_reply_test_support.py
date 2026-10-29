"""Admit receipts when tests invoke a worker directly rather than via webhook."""
from datetime import datetime
from sqlalchemy.exc import IntegrityError
import global_ai_control
from models import ProcessedWhatsAppMessage


def admit(sessions, phone, message_id):
    with sessions() as db:
        if db.query(ProcessedWhatsAppMessage).filter_by(message_id=message_id).first():
            return
        db.add(ProcessedWhatsAppMessage(message_id=message_id, sender_phone=phone,
            message_type="text", received_at=datetime.utcnow(),
            ai_reply_generation=global_ai_control.admission(sessions)))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()  # Concurrent test workers may admit the same receipt.


def reply_text(main, sender, message_id, *args, **kwargs):
    admit(main.SessionLocal, sender, message_id)
    return main._reply_to_text_message(sender, message_id, *args, **kwargs)


def deliver_products(module, sessions, phone, message_id, *args, **kwargs):
    admit(sessions, phone, message_id)
    return module.deliver(sessions, phone, message_id, *args, **kwargs)


def product_message(main, phone, product, text):
    message_id = "unit-product-send"
    admit(main.SessionLocal, phone, message_id)
    @global_ai_control.automatic_job(lambda: main.SessionLocal)
    def run(sender, message_id):
        return main._send_product_message(sender, product, text)
    return run(phone, message_id)
