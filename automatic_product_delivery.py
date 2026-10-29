"""Durable, at-most-once automatic Shopify image attachment attempts.

No retry after an ambiguous provider outcome. A claimed inbound batch is never
replayed, including after a restart or database failure following a provider send.
"""

import global_ai_control
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from time import monotonic

from sqlalchemy.exc import IntegrityError

from bot_configuration_service import simulation_preferences
from models import (BotConfiguration, WhatsAppConversation, WhatsAppConversationMessage,
                    WhatsAppManualSend, WhatsAppOutboundProductMessage)
from shopify_collection_service import safe_image

logger = logging.getLogger(__name__)


def _allowed(db, conversation):
    if not conversation or conversation.mode != "AI" or not conversation.last_customer_message_at:
        return False
    last = conversation.last_customer_message_at
    if last.tzinfo is not None:
        last = last.astimezone(timezone.utc).replace(tzinfo=None)
    if datetime.utcnow() - last >= timedelta(hours=24):
        return False
    config = db.query(BotConfiguration).first()
    if not config:
        return False
    prefs = simulation_preferences(config.published_snapshot)
    return prefs["automatic_product_attachments"] and prefs["send_product_images"]


def _stock(product):
    quantity = product.get("inventory_quantity")
    return (product.get("published") is True and product.get("available") is True
        and product.get("inventory_tracked") is True and type(quantity) is int and quantity > 0
        and safe_image(product.get("image")))


def can_deliver(sessions, phone):
    with sessions() as db:
        conversation = db.query(WhatsAppConversation).filter_by(phone_number=phone).one_or_none()
        return _allowed(db, conversation)


def caption(product):
    variant = str(product.get("variant_title") or "")
    extra = "\n" + variant if variant and variant != "Default Title" else ""
    return f"{product['title']}{extra}\nRs. {product['price']:,.2f}\n{product['url']}"


@global_ai_control.automatic_job(None, message_index=2, sender_index=1, sessions_index=0)
def deliver(sessions, phone, inbound_message_id, products, resolve, send_image):
    """Send up to ten cards; callers provide server-verified catalog selections."""
    key = "apb_" + hashlib.sha256(f"{phone}:{inbound_message_id}".encode()).hexdigest()
    try:
        with sessions() as db:
            conversation = db.query(WhatsAppConversation).filter_by(phone_number=phone).one_or_none()
            if not _allowed(db, conversation):
                return []
            config = db.query(BotConfiguration).first()
            maximum = simulation_preferences(config.published_snapshot)["maximum_products"]
            products = products[:maximum]
            root = WhatsAppManualSend(idempotency_key=key, conversation_id=conversation.id,
                message_text=json.dumps({"kind": "automatic_product_batch", "inbound": inbound_message_id,
                    "handles": [p["handle"] for p in products]}), status="PROCESSING", sent_by="bot")
            db.add(root)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return []  # Another worker/restart already claimed this inbound.
        sent_handles = []
        started = monotonic()
        for index, selected in enumerate(products):
            if monotonic() - started > 90:
                break
            with sessions() as db:
                conversation = db.query(WhatsAppConversation).filter_by(phone_number=phone).one_or_none()
                if not _allowed(db, conversation):
                    break
                # A newer inbound turn supersedes this selection, including a
                # requirement change while the Shopify/provider requests ran.
                latest = db.query(WhatsAppConversationMessage).filter_by(
                    phone_number=phone, direction="inbound").order_by(WhatsAppConversationMessage.id.desc()).first()
                if latest and latest.whatsapp_message_id != inbound_message_id:
                    break
                attempt = WhatsAppManualSend(idempotency_key=f"{key}_{index}",
                    conversation_id=conversation.id, message_text=selected["handle"],
                    status="VERIFYING", sent_by="bot")
                db.add(attempt)
                db.commit()
                variant_id = selected.get("selected_variant_id")
                original = next((v for v in selected.get("variants", []) if v.get("id") == variant_id), None)
                fresh = resolve(selected.get("id"), variant_id)
                if (not original or not fresh or not _stock(fresh)
                        or fresh.get("id") != selected.get("id") or fresh.get("variant_id") != variant_id
                        or fresh.get("handle") != selected["handle"]
                        or fresh.get("title") != selected.get("title")
                        or fresh.get("variant_title") != original.get("title")
                        or Decimal(str(fresh["price"])) != Decimal(str(original["price"]))):
                    attempt.status = "SKIPPED"
                    db.commit()
                    continue
                body = caption(fresh)
                if len(body) > 1024:
                    attempt.status = "SKIPPED"
                    db.commit()
                    continue
                # Refresh mode/publication/window after the network lookup too.
                db.refresh(conversation)
                db.expire_all()
                if not _allowed(db, conversation):
                    attempt.status = "SKIPPED"
                    db.commit()
                    break
                latest = db.query(WhatsAppConversationMessage).filter_by(
                    phone_number=phone, direction="inbound").order_by(WhatsAppConversationMessage.id.desc()).first()
                current = db.query(BotConfiguration).first()
                if ((latest and latest.whatsapp_message_id != inbound_message_id)
                        or index >= simulation_preferences(current.published_snapshot)["maximum_products"]):
                    attempt.status = "SKIPPED"
                    db.commit()
                    break
                attempt.message_text, attempt.status = body, "PROCESSING"
                db.commit()  # Durable ambiguous state BEFORE irreversible provider I/O.
                try:
                    with global_ai_control.send_boundary(require_job=True):
                        state, message_id = send_image(phone, fresh["image"], body)
                except global_ai_control.AutoReplyBlocked:
                    attempt.status = "SKIPPED"
                    db.commit()
                    break
                except Exception:
                    state, message_id = "UNKNOWN", None
                if state != "SENT" or not isinstance(message_id, str) or not message_id.strip():
                    attempt.status = "FAILED" if state == "FAILED" else "UNKNOWN"
                    db.commit()
                    break  # Never fall back to text or retry an ambiguous image send.
                attempt.status, attempt.whatsapp_message_id = "SENT", message_id
                db.add(WhatsAppConversationMessage(phone_number=phone, direction="outbound", message_text=body,
                    whatsapp_message_id=message_id, response_kind="ai_product", send_status="sent", sent_by="bot",
                    shopify_product_reference=fresh["id"], shopify_variant_reference=fresh["variant_id"],
                    product_handle=fresh["handle"], product_title=fresh["title"], variant_title=fresh.get("variant_title"),
                    product_sku=fresh.get("sku"), product_price=fresh["price"], product_currency=fresh["currency"],
                    product_image_url=fresh["image"], product_url=fresh["url"]))
                db.add(WhatsAppOutboundProductMessage(whatsapp_message_id=message_id, customer_phone=phone,
                    shopify_product_handle=fresh["handle"], product_title=fresh["title"]))
                conversation.last_message_at = datetime.utcnow()
                db.commit()
                sent_handles.append(fresh["handle"])
        with sessions() as db:
            root = db.query(WhatsAppManualSend).filter_by(idempotency_key=key).one()
            root.status = "COMPLETE" if len(sent_handles) == len(products) else "PARTIAL"
            db.commit()
        return sent_handles
    except Exception as error:
        # The root claim remains durable. A later duplicate cannot repeat sends.
        logger.warning("Automatic product batch stopped error_type=%s", type(error).__name__)
        return []
