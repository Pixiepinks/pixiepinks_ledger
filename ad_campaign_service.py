"""Published, exact-ad first replies and bounded follow-up campaign context."""
import hashlib
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from models import BotConfiguration, WhatsAppMessageReferral, WhatsAppConversationMessage, WhatsAppManualSend
from quick_message_service import require_reply_window

EXAMPLE = {
    "source_id": "52608663935249", "title": "Baby Care Gift Pack", "enabled": True,
    "pack_price": "3750.00", "delivery_price": "400.00", "expires_on": "2026-12-31",
    "first_reply": "Hi Dear, me pack eka wenawa Rs.3,750. Mekata delivery add wenawa Rs.400.\n\nOrder eka proceed karannada?",
    "contents": "Pears Mermaid Princess Baby Cologne\nPears Baby Shampoo\nPears Rathmal Baby Soap\nPears Rathmal Baby Cream\nPears Pure & Gentle Baby Cream\nBaby Cheramy Laundry Wash Liquid\nBaby Cheramy Floral Laundry Wash Powder\niBaby Disposable Diapers",
}


def validate_rules(rules):
    errors, seen = [], set()
    if not isinstance(rules, list) or len(rules) > 100: return ["Ad replies must contain at most 100 rules."]
    for rule in rules:
        if not isinstance(rule, dict): errors.append("Invalid ad reply rule."); continue
        source = rule.get("source_id")
        if not isinstance(source, str): errors.append("Ad IDs must be numeric strings."); continue
        if not re.fullmatch(r"[0-9]{1,32}", source) or source in seen:
            errors.append("Ad replies require distinct numeric ad IDs.")
        seen.add(source)
        for key, limit in (("title",100), ("first_reply",1200), ("contents",2000)):
            value = rule.get(key)
            if not isinstance(value, str) or not 0 < len(value.strip()) <= limit:
                errors.append(f"Ad reply {key} must contain 1–{limit} characters.")
        if type(rule.get("enabled")) is not bool: errors.append("Ad enabled must be a boolean.")
        try: date.fromisoformat(rule.get("expires_on", ""))
        except (TypeError, ValueError): errors.append("Ad replies require a valid offer expiry date.")
        for key in ("pack_price", "delivery_price"):
            try:
                value = Decimal(str(rule.get(key)))
                if not value.is_finite() or not 0 <= value <= 1000000: raise ValueError
            except (InvalidOperation, ValueError, TypeError): errors.append("Ad prices must be finite nonnegative amounts.")
    return errors


def rule_for_source(snapshot, source_id, today=None):
    today = today or datetime.now(ZoneInfo("Asia/Colombo")).date()
    for rule in snapshot.get("ad_replies", []):
        if rule.get("source_id") != source_id or not rule.get("enabled", False):
            continue
        try:
            if date.fromisoformat(rule["expires_on"]) < today: return None
        except (KeyError, ValueError, TypeError): return None
        return rule
    return None


def conversation_campaign(db, phone, inbound_id, message):
    """Match stored referral metadata, never IDs typed in customer text or captions."""
    inbound = db.query(WhatsAppConversationMessage).filter_by(phone_number=phone, direction="inbound",
        whatsapp_message_id=inbound_id).one_or_none()
    if inbound is None: return None
    referral = db.query(WhatsAppMessageReferral).join(WhatsAppConversationMessage,
        WhatsAppMessageReferral.message_id == WhatsAppConversationMessage.id).filter(
            WhatsAppConversationMessage.phone_number == phone,
            WhatsAppConversationMessage.direction == "inbound",
            WhatsAppConversationMessage.id <= inbound.id,
            WhatsAppConversationMessage.created_at >= datetime.utcnow() - timedelta(hours=24),
        ).order_by(WhatsAppConversationMessage.id.desc()).first()
    if referral is None or (referral.source_type or "").casefold() != "ad": return None
    config = db.query(BotConfiguration).first()
    if config is None: return None
    rule = rule_for_source(config.published_snapshot or {}, referral.source_id)
    if rule is None: return None
    first = referral.message_id == inbound.id
    # A new unrelated shopping request should leave the gift-pack context.
    if not first:
        intervening = db.query(WhatsAppConversationMessage.message_text).filter(
            WhatsAppConversationMessage.phone_number == phone, WhatsAppConversationMessage.direction == "inbound",
            WhatsAppConversationMessage.id > referral.message_id, WhatsAppConversationMessage.id <= inbound.id,
        ).limit(201).all()
        if len(intervening) > 200 or any(re.search(
            r"\bbicycles?\b|\bbikes?\b|water\s*bottles?|school\s*bags?|\btoys?\b|\bchocolates?\b|\bstationery\b|බයිසික",
            text or "", re.I) for text, in intervening): return None
    return {"rule": rule, "referral_message_id": referral.message_id, "first": first,
            "published_version": config.published_version}


def followup_context(campaign):
    rule = campaign["rule"]
    return {**{key: rule.get(key) for key in ("title", "pack_price", "delivery_price", "expires_on", "contents")},
            "offer_total": str(Decimal(rule["pack_price"]) + Decimal(rule["delivery_price"]))}


def send_reply(db, conversation, campaign, inbound_id, message, profile_name, send_text, generate):
    """At-most-once campaign reply for this inbound, including AI follow-ups."""
    key = "adc_" + hashlib.sha256(f"{conversation.id}:{inbound_id}".encode()).hexdigest()
    if db.query(WhatsAppManualSend).filter_by(idempotency_key=key).first(): return
    require_reply_window(db, conversation)
    reply = campaign["rule"]["first_reply"] if campaign["first"] else "Campaign follow-up"
    attempt = WhatsAppManualSend(idempotency_key=key, conversation_id=conversation.id,
        message_text=reply, status="PROCESSING", sent_by="bot")
    db.add(attempt)
    try: db.commit()
    except IntegrityError: db.rollback(); return
    if not campaign["first"]:
        history = db.query(WhatsAppConversationMessage).filter(
            WhatsAppConversationMessage.phone_number == conversation.phone_number,
            WhatsAppConversationMessage.whatsapp_message_id != inbound_id,
        ).order_by(WhatsAppConversationMessage.id.desc()).limit(8).all()
        context = [{"direction": item.direction, "message_text": item.message_text} for item in reversed(history)]
        reply = generate(message, profile_name, context, campaign_context=followup_context(campaign))
    db.refresh(conversation)
    if conversation.mode != "AI":
        attempt.status = "SKIPPED"; db.commit(); return
    try: require_reply_window(db, conversation)
    except HTTPException:
        attempt.status = "SKIPPED"; db.commit(); return
    db.expire_all()
    config = db.query(BotConfiguration).first()
    current = rule_for_source(config.published_snapshot or {}, campaign["rule"]["source_id"]) if config else None
    if current != campaign["rule"]:
        attempt.status = "SKIPPED"; db.commit(); return
    attempt.message_text = reply
    try: message_id = send_text(conversation.phone_number, reply)
    except Exception: message_id = None
    if not isinstance(message_id, str) or not message_id.strip():
        attempt.status = "UNKNOWN"; db.commit(); return
    attempt.status, attempt.whatsapp_message_id = "SENT", message_id
    db.add(WhatsAppConversationMessage(phone_number=conversation.phone_number, direction="outbound",
        message_text=reply, whatsapp_message_id=message_id, response_kind="ad_first_reply" if campaign["first"] else "ad_followup",
        send_status="sent", sent_by="bot"))
    conversation.last_message_at = datetime.utcnow()
    db.commit()
