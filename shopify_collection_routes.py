"""Staff-reviewed Shopify collections sent through the existing image workflow."""
import asyncio
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.concurrency import run_in_threadpool

import shopify_collection_service as service
from models import WhatsAppConversation, WhatsAppManualSend
from quick_message_service import STAFF_ROLES, require_reply_window
from settings import settings
from shopify_catalog_service import ShopifyCatalogClient, ShopifyCatalogError, discover_collections


def signer():
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt="shopify-image-collection-v1")


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def item_key(batch, product_id):
    return "sci_" + service.fingerprint([batch.idempotency_key, product_id])


def authorize(user):
    if user.role not in STAFF_ROLES:
        raise HTTPException(403, "Staff access is required")


class CollectionRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def safe(request):
            try:
                response = await original(request)
            except service.CollectionError as error:
                response = JSONResponse({"detail": str(error)}, status_code=409)
            except ShopifyCatalogError:
                response = JSONResponse({"detail": "Shopify verification is unavailable. No new send was started."}, status_code=503)
            except SQLAlchemyError:
                response = JSONResponse({"detail": "Database operation failed. Check the existing batch status; do not create another batch."}, status_code=503)
            except HTTPException as error:
                response = JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)
            except TimeoutError:
                response = JSONResponse({"detail": "Request timed out. Check the existing batch status."}, status_code=408)
            response.headers["Cache-Control"] = "private, no-store"
            return response
        return safe


async def read_json(request):
    raw = bytearray()
    async with asyncio.timeout(10):
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 16384:
                raise HTTPException(413, "Request is too large")
            raw.extend(chunk)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(422, "A JSON object is required")
    return body


def request_key(request):
    key = request.headers.get("Idempotency-Key", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key):
        raise HTTPException(400, "A valid Idempotency-Key is required")
    return key


def get_conversation(db, cid):
    row = db.get(WhatsAppConversation, cid)
    if row is None:
        raise HTTPException(404, "Conversation not found")
    return row


def get_batch(db, cid, batch_id, user):
    row = db.get(WhatsAppManualSend, batch_id)
    if (row is None or row.conversation_id != cid or row.sent_by != user.username
            or not row.idempotency_key.startswith("scb_")):
        raise HTTPException(404, "Collection batch not found")
    data = json.loads(row.message_text)
    if data.get("kind") != "shopify_image_collection" or data.get("actor") != user.id:
        raise HTTPException(404, "Collection batch not found")
    return row, data


def children(db, batch, data):
    keys = [item_key(batch, item["id"]) for item in data["items"]]
    return {row.idempotency_key: row for row in db.query(WhatsAppManualSend).filter(
        WhatsAppManualSend.idempotency_key.in_(keys)).all()}


def state(db, batch, data):
    rows = children(db, batch, data)
    items = []
    for expected in data["items"]:
        row = rows[item_key(batch, expected["id"])]
        detail = json.loads(row.message_text)
        items.append({"product_id": expected["id"], "title": expected["title"],
            "variant_title": expected["variant_title"], "status": row.status,
            "reason": detail.get("reason", "")})
    return {"batch_id": batch.id, "conversation_id": batch.conversation_id, "status": batch.status,
            "collection": data["collection"], "items": items,
            "counts": {status: sum(item["status"] == status for item in items)
                       for status in ["QUEUED", "VERIFYING", "PROCESSING", "SENT", "FAILED", "SKIPPED", "UNKNOWN"]}}


def finish(db, batch, data):
    statuses = {row.status for row in children(db, batch, data).values()}
    batch.status = ("BLOCKED" if statuses & {"UNKNOWN", "PROCESSING", "VERIFYING"} else
                    "READY" if "QUEUED" in statuses else
                    "COMPLETE" if statuses == {"SENT"} else "PARTIAL")
    db.commit()
    return state(db, batch, data)


def slot():
    if not service.SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Product verification is busy. Retry this same action shortly.")


def build_router(get_db, require_user, require_csrf, csrf_token, resolve_product, send_image, caption, record_product):
    router = APIRouter(prefix="/crm/whatsapp/api", route_class=CollectionRoute)

    @router.get("/collections")
    def collection_search(request: Request, q: str = Query("", max_length=100), user=Depends(require_user)):
        authorize(user)
        slot()
        try:
            return {"collections": discover_collections(q, 100), "csrf_token": csrf_token(request)}
        finally:
            service.SLOTS.release()

    @router.get("/conversations/{cid}/collection-preview")
    def preview(cid: int, request: Request,
            collection_id: str = Query(..., max_length=100, pattern=r"^gid://shopify/Collection/[0-9]{1,30}$"),
            db=Depends(get_db), user=Depends(require_user)):
        authorize(user)
        require_reply_window(db, get_conversation(db, cid))
        slot()
        try:
            result = service.load_collection(collection_id)
        finally:
            service.SLOTS.release()
        token = signer().dumps({"actor": user.id, "conversation": cid, "collection": collection_id,
                                "snapshot": service.fingerprint(result)})
        return {**result, "preview_token": token, "csrf_token": csrf_token(request)}

    def create_batch(cid, db, user, body, key):
        conversation = get_conversation(db, cid)
        key = "scb_" + service.fingerprint(key)
        scope = service.fingerprint(body)
        existing = db.query(WhatsAppManualSend).filter_by(idempotency_key=key).one_or_none()
        if existing:
            batch, data = get_batch(db, cid, existing.id, user)
            if data["request"] != scope:
                raise HTTPException(409, "Request key belongs to another selection")
            return state(db, batch, data)
        require_reply_window(db, conversation)
        try:
            signed = signer().loads(body["preview_token"], max_age=600)
        except BadSignature:
            raise HTTPException(409, "Preview expired or invalid. Refresh and review the collection.") from None
        if (signed.get("actor") != user.id or signed.get("conversation") != cid
                or signed.get("collection") != body["collection_id"]):
            raise HTTPException(409, "Preview belongs to another staff member or conversation")
        slot()
        try:
            current = service.load_collection(body["collection_id"])
        finally:
            service.SLOTS.release()
        if service.fingerprint(current) != signed.get("snapshot"):
            raise HTTPException(409, "Collection products, prices or availability changed. Refresh and review before sending.")
        # Serialize new batch creation on PostgreSQL and prevent a fresh request
        # key from abandoning an unfinished/unconfirmed batch for this customer.
        db.refresh(conversation, with_for_update=True)
        existing = db.query(WhatsAppManualSend).filter_by(idempotency_key=key).one_or_none()
        if existing:
            batch, data = get_batch(db, cid, existing.id, user)
            if data["request"] != scope:
                raise HTTPException(409, "Request key belongs to another selection")
            return state(db, batch, data)
        active = db.query(WhatsAppManualSend).filter(
            WhatsAppManualSend.conversation_id == cid, WhatsAppManualSend.sent_by == user.username,
            WhatsAppManualSend.idempotency_key.like("scb\\_%", escape="\\"),
            WhatsAppManualSend.status.in_(["READY", "PROCESSING", "UPDATING", "BLOCKED"])).first()
        if active:
            raise HTTPException(409, "An unfinished collection batch exists. Reopen the collection picker and check or resume it.")
        allowed = {(product["id"], variant["variant_id"]): variant for product in current["products"] for variant in product["variants"]}
        selected = []
        for item in body["items"]:
            product = allowed.get((item["product_id"], item["variant_id"]))
            if product is None:
                raise HTTPException(422, "Select only available variants from this collection")
            selected.append(product)
        batch = WhatsAppManualSend(idempotency_key=key, conversation_id=cid, sent_by=user.username,
            status="READY", message_text=encode({"kind": "shopify_image_collection", "actor": user.id,
                "request": scope, "collection": current["collection"], "items": selected}))
        db.add(batch)
        for product in selected:
            db.add(WhatsAppManualSend(idempotency_key=item_key(batch, product["id"]), conversation_id=cid,
                sent_by=user.username, status="QUEUED", message_text=encode({"reason": ""})))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.query(WhatsAppManualSend).filter_by(idempotency_key=key).one_or_none()
            if not existing:
                raise
            batch, data = get_batch(db, cid, existing.id, user)
            if data["request"] != scope:
                raise HTTPException(409, "Request key belongs to another selection")
            return state(db, batch, data)
        return state(db, batch, json.loads(batch.message_text))

    @router.post("/conversations/{cid}/collection-batches")
    async def prepare(cid: int, request: Request, db=Depends(get_db), user=Depends(require_user)):
        authorize(user); require_csrf(request)
        key = request_key(request); body = await read_json(request)
        if (set(body) != {"collection_id", "preview_token", "items"}
                or not service.valid_id(body["collection_id"], "Collection")
                or not isinstance(body["preview_token"], str) or not 0 < len(body["preview_token"]) <= 4096
                or not isinstance(body["items"], list) or not 1 <= len(body["items"]) <= service.MAX_PRODUCTS
                or any(not isinstance(item, dict) or set(item) != {"product_id", "variant_id"}
                       or not service.valid_id(item["product_id"], "Product")
                       or not service.valid_id(item["variant_id"], "ProductVariant") for item in body["items"])
                or len({item["product_id"] for item in body["items"]}) != len(body["items"])):
            raise HTTPException(422, "Choose 1–20 products and exactly one available variant per product")
        return await run_in_threadpool(create_batch, cid, db, user, body, key)

    @router.get("/conversations/{cid}/collection-batches/recent")
    def recent(cid: int, request: Request, db=Depends(get_db), user=Depends(require_user)):
        authorize(user); get_conversation(db, cid)
        row = db.query(WhatsAppManualSend).filter(WhatsAppManualSend.conversation_id == cid,
            WhatsAppManualSend.sent_by == user.username,
            WhatsAppManualSend.idempotency_key.like("scb\\_%", escape="\\")).order_by(WhatsAppManualSend.id.desc()).first()
        if row:
            batch, data = get_batch(db, cid, row.id, user)
            return {"batch": state(db, batch, data), "csrf_token": csrf_token(request)}
        return {"batch": None, "csrf_token": csrf_token(request)}

    @router.get("/conversations/{cid}/collection-batches/{batch_id}")
    def progress(cid: int, batch_id: int, db=Depends(get_db), user=Depends(require_user)):
        authorize(user)
        batch, data = get_batch(db, cid, batch_id, user)
        return state(db, batch, data)

    def send_one(cid, batch_id, product_id, db, user):
        batch, data = get_batch(db, cid, batch_id, user)
        expected = next((item for item in data["items"] if item["id"] == product_id), None)
        if expected is None:
            raise HTTPException(422, "Product is not part of this batch")
        row = children(db, batch, data)[item_key(batch, product_id)]
        if row.status != "QUEUED":
            return state(db, batch, data)
        conversation = get_conversation(db, cid)
        require_reply_window(db, conversation)
        slot()
        try:
            claimed = db.query(WhatsAppManualSend).filter_by(id=batch.id, status="READY").update(
                {"status": "PROCESSING"}, synchronize_session=False)
            if not claimed:
                db.rollback(); db.refresh(batch)
                return state(db, batch, data)
            db.refresh(row); db.refresh(batch)
            if row.status != "QUEUED":
                return finish(db, batch, data)
            row.status = "VERIFYING"
            db.commit()
            # A durable claim precedes all external work. Interrupted VERIFYING or
            # PROCESSING work is never automatically re-sent.
            try:
                client = ShopifyCatalogClient()
                live = service.load_collection(data["collection"]["id"], client)
                member = any(product["id"] == product_id and any(
                    item["variant_id"] == expected["variant_id"] for item in product["variants"])
                    for product in live["products"])
                product = resolve_product(product_id, expected["variant_id"], client) if member else None
            except (ShopifyCatalogError, service.CollectionError):
                row.status, row.message_text = "FAILED", encode({"reason": "Verification unavailable; no customer message sent."})
                return finish(db, batch, data)
            if product is None or not service.available(product):
                row.status, row.message_text = "SKIPPED", encode({"reason": "No longer available, published or in this collection."})
                return finish(db, batch, data)
            if service.snapshot(product) != expected:
                row.status, row.message_text = "SKIPPED", encode({"reason": "Product details or price changed; refresh and review before sending."})
                return finish(db, batch, data)
            db.refresh(conversation)
            try:
                require_reply_window(db, conversation)
            except HTTPException:
                row.status, row.message_text = "SKIPPED", encode({"reason": "Customer-service window closed before sending."})
                return finish(db, batch, data)
            if len(caption(product)) > 1024:
                row.status, row.message_text = "SKIPPED", encode({"reason": "Product caption exceeds WhatsApp's image-caption limit."})
                return finish(db, batch, data)
            row.status = "PROCESSING"
            db.commit()
            try:
                outcome, message_id = send_image(conversation.phone_number, product["image"], caption(product))
            except Exception:
                outcome, message_id = "UNKNOWN", None
            if outcome == "SENT" and isinstance(message_id, str) and message_id:
                # Persist exactly the same product metadata/reply mapping used by
                # existing single-product sends and delivery-status webhooks.
                record_product(db, conversation, user, product, row, message_id)
            elif outcome == "FAILED":
                row.status, row.message_text = "FAILED", encode({"reason": "Meta rejected this image; no confirmed message sent."})
            else:
                row.status, row.message_text = "UNKNOWN", encode({"reason": "Delivery outcome unconfirmed. Do not resend; operator review required."})
            return finish(db, batch, data)
        finally:
            service.SLOTS.release()

    @router.post("/conversations/{cid}/collection-batches/{batch_id}/send-item")
    async def item_send(cid: int, batch_id: int, request: Request, db=Depends(get_db), user=Depends(require_user)):
        authorize(user); require_csrf(request)
        body = await read_json(request)
        if set(body) != {"product_id"} or not service.valid_id(body["product_id"], "Product"):
            raise HTTPException(422, "Choose a product in this batch")
        return await run_in_threadpool(send_one, cid, batch_id, body["product_id"], db, user)

    @router.post("/conversations/{cid}/collection-batches/{batch_id}/retry-failed")
    def retry_failed(cid: int, batch_id: int, request: Request, db=Depends(get_db), user=Depends(require_user)):
        authorize(user); require_csrf(request)
        batch, data = get_batch(db, cid, batch_id, user)
        retry_key = "scr_" + service.fingerprint(request_key(request))
        scope = encode({"kind": "retry_failed_collection", "batch": batch.id, "actor": user.id})
        prior = db.query(WhatsAppManualSend).filter_by(idempotency_key=retry_key).one_or_none()
        if prior:
            if prior.message_text != scope:
                raise HTTPException(409, "Retry key belongs to another action")
            return state(db, batch, data)
        if not db.query(WhatsAppManualSend).filter(WhatsAppManualSend.id == batch.id,
                WhatsAppManualSend.status.in_(["PARTIAL", "READY"])).update({"status": "UPDATING"}, synchronize_session=False):
            db.rollback(); raise HTTPException(409, "Batch is busy or has an unconfirmed outcome; do not retry it")
        for row in children(db, batch, data).values():
            if row.status == "FAILED":
                row.status, row.message_text = "QUEUED", encode({"reason": ""})
        try:
            db.add(WhatsAppManualSend(idempotency_key=retry_key, conversation_id=cid, sent_by=user.username,
                status="SENT", message_text=scope))
            db.refresh(batch)
            return finish(db, batch, data)
        except IntegrityError:
            db.rollback()
            prior = db.query(WhatsAppManualSend).filter_by(idempotency_key=retry_key).one_or_none()
            if not prior or prior.message_text != scope:
                raise HTTPException(409, "Retry key belongs to another action") from None
            db.refresh(batch)
            return state(db, batch, data)

    return router
