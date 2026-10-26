"""Staff-only, CSRF-protected collection preview and explicit send workflow."""
import asyncio
import hashlib
import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.concurrency import run_in_threadpool

import whatsapp_collection_service as service
from models import WhatsAppConversation, WhatsAppConversationMessage, WhatsAppManualSend
from quick_message_service import STAFF_ROLES, require_reply_window
from settings import settings
from shopify_catalog_service import ShopifyCatalogError, discover_collections


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def signer():
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="whatsapp-collection-preview-v1")


class CollectionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe(request):
            try:
                response = await handler(request)
            except service.CollectionError as error:
                # These are fixed strings, never provider response bodies.
                response = JSONResponse({"detail": str(error)}, status_code=503)
            except ShopifyCatalogError:
                response = JSONResponse({"detail": "Shopify collection verification is temporarily unavailable."}, status_code=503)
            except SQLAlchemyError:
                response = JSONResponse({"detail": "Database operation failed. Do not start another send; check the conversation and retry with the same request key."}, status_code=503)
            except HTTPException as error:
                response = JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)
            except TimeoutError:
                response = JSONResponse({"detail": "Collection request timed out."}, status_code=408)
            response.headers["Cache-Control"] = "private, no-store"
            return response
        return safe


def authorize(user):
    if user.role not in STAFF_ROLES:
        raise HTTPException(403, "Staff access is required")


def conversation(db, cid):
    row = db.get(WhatsAppConversation, cid)
    if row is None:
        raise HTTPException(404, "Conversation not found")
    return row


def reserve_slot():
    if not service.SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Collection service is busy. Try again shortly.")


def build_router(get_db, require_user, require_csrf, csrf_token):
    router = APIRouter(prefix="/crm/whatsapp/api", route_class=CollectionRoute)

    @router.get("/collections")
    def collections(request: Request, q: str = Query("", max_length=100), user=Depends(require_user)):
        authorize(user)
        # Configuration status is safe and actionable, without returning IDs,
        # mappings, tokens or provider response bodies.
        try:
            service.configuration()
        except service.CollectionError as error:
            return {"collections": [], "configured": False, "detail": str(error), "csrf_token": csrf_token(request)}
        reserve_slot()
        try:
            rows = discover_collections(q, 100)
            return {"collections": rows, "configured": True, "csrf_token": csrf_token(request),
                    "limit": 100, "search_hint": "Search to narrow the first 100 collections."}
        finally:
            service.SLOTS.release()

    @router.get("/conversations/{cid}/collection-preview")
    def collection_preview(cid: int, request: Request,
            collection_id: str = Query(..., max_length=100, pattern=r"^gid://shopify/Collection/[0-9]+$"),
            db=Depends(get_db), user=Depends(require_user)):
        authorize(user)
        row = conversation(db, cid)
        require_reply_window(db, row)
        reserve_slot()
        try:
            result = service.preview(collection_id)
        finally:
            service.SLOTS.release()
        token = signer().dumps({"actor": user.id, "conversation": cid, "collection": collection_id,
                                "catalog": settings.META_WHATSAPP_CATALOG_ID, "snapshot": fingerprint(result)})
        return {**result, "items": [{key: value for key, value in item.items() if key != "retailer_id"}
                                    for item in result["items"]],
                "preview_token": token, "max_items": service.MAX_ITEMS, "csrf_token": csrf_token(request)}

    def perform_send(cid, db, user, body, key):
        row = conversation(db, cid)
        scope = json.dumps({"kind": "collection", "actor": user.id, "conversation": cid,
                            "body": fingerprint(body)}, sort_keys=True)

        def previous(attempt):
            if attempt.conversation_id != cid or attempt.sent_by != user.username or attempt.message_text != scope:
                raise HTTPException(409, "Idempotency key belongs to another action")
            return JSONResponse({"ok": attempt.status == "SENT", "status": attempt.status,
                "detail": "Collection sent." if attempt.status == "SENT" else
                    "Send was rejected." if attempt.status == "FAILED" else
                    "Send outcome is unconfirmed. Do not send again with a new key; check the conversation."},
                status_code=200 if attempt.status == "SENT" else 409)

        existing = db.query(WhatsAppManualSend).filter_by(idempotency_key=key).one_or_none()
        if existing:
            return previous(existing)
        try:
            signed = signer().loads(body["preview_token"], max_age=600)
        except (BadSignature, SignatureExpired):
            raise HTTPException(409, "Preview expired or invalid. Refresh the collection preview.") from None
        if (signed.get("actor") != user.id or signed.get("conversation") != cid
                or signed.get("collection") != body["collection_id"]
                or signed.get("catalog") != settings.META_WHATSAPP_CATALOG_ID):
            raise HTTPException(409, "Preview belongs to another conversation or configuration. Refresh it.")
        require_reply_window(db, row)
        reserve_slot()
        try:
            current = service.preview(body["collection_id"])
            if fingerprint(current) != signed.get("snapshot"):
                raise HTTPException(409, "Products, stock, prices or catalog mapping changed. Refresh and review the collection before sending.")
            by_id = {item["variant_id"]: item for item in current["items"]}
            if any(variant not in by_id for variant in body["variant_ids"]):
                raise HTTPException(422, "Select only verified available variants from this collection")
            items = [by_id[variant] for variant in body["variant_ids"]]
            # Re-read the customer-service timestamp after slow catalog checks.
            db.refresh(row)
            require_reply_window(db, row)
            attempt = WhatsAppManualSend(idempotency_key=key, conversation_id=cid,
                sent_by=user.username, message_text=scope, status="PROCESSING")
            db.add(attempt)
            try:
                # Persist the unique reservation BEFORE any external send. A crash
                # or ambiguous network result must never cause automatic resending.
                db.commit()
            except IntegrityError:
                db.rollback()
                competing = db.query(WhatsAppManualSend).filter_by(idempotency_key=key).one_or_none()
                if competing:
                    return previous(competing)
                raise
            state, message_id = service.send_product_list(row.phone_number, current["collection"]["title"], items)
            if state != "SENT":
                attempt.status = "FAILED" if state == "FAILED" else "PROCESSING"
                db.commit()
                return JSONResponse({"ok": False, "status": attempt.status,
                    "detail": "Meta rejected the collection. Check catalog access and product eligibility." if state == "FAILED" else
                        "Send outcome is unconfirmed. Do not resend; check the conversation before contacting support."}, status_code=502)
            summary = "Collection: " + current["collection"]["title"] + "\n" + "\n".join(
                f"• {item['title']} — {item['variant_title']}\n{item['url']}" for item in items)
            attempt.status, attempt.whatsapp_message_id = "SENT", message_id
            db.add(WhatsAppConversationMessage(phone_number=row.phone_number, direction="outbound",
                message_text=summary, whatsapp_message_id=message_id, response_kind="staff_product",
                send_status="sent", sent_by=user.username))
            row.last_message_at = datetime.utcnow()
            db.commit()
            return {"ok": True, "status": "SENT", "count": len(items)}
        finally:
            service.SLOTS.release()

    @router.post("/conversations/{cid}/send-collection")
    async def send_collection(cid: int, request: Request, db=Depends(get_db), user=Depends(require_user)):
        authorize(user)
        require_csrf(request)
        key = request.headers.get("Idempotency-Key", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key):
            raise HTTPException(400, "A valid Idempotency-Key is required")
        raw = bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 16384:
                    raise HTTPException(413, "Collection request is too large")
                raw.extend(chunk)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid JSON") from None
        if (not isinstance(body, dict) or set(body) != {"collection_id", "variant_ids", "preview_token"}
                or not isinstance(body["collection_id"], str)
                or not re.fullmatch(r"gid://shopify/Collection/[0-9]+", body["collection_id"])
                or not isinstance(body["preview_token"], str) or not 1 <= len(body["preview_token"]) <= 4096
                or not isinstance(body["variant_ids"], list) or not 1 <= len(body["variant_ids"]) <= service.MAX_ITEMS
                or any(not isinstance(value, str) or not re.fullmatch(r"gid://shopify/ProductVariant/[0-9]+", value)
                       for value in body["variant_ids"])
                or len(set(body["variant_ids"])) != len(body["variant_ids"])):
            raise HTTPException(422, "Select 1–30 unique available variants from a current collection preview")
        return await run_in_threadpool(perform_send, cid, db, user, body, key)

    return router
