import hashlib
import hmac
import json
import logging
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlparse

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func, inspect, text, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from settings import settings
from database import SessionLocal, engine
from models import (
    Account,
    Base,
    CRMUser,
    Customer,
    Item,
    JournalEntry,
    JournalLine,
    Lead,
    LeadNote,
    LeadTask,
    ProcessedWhatsAppMessage,
    Supplier,
    User,
    WhatsAppConversationMessage,
)
from ai_service import FALLBACK_REPLY, generate_customer_reply
from seed import init_db
from utils_auth import hash_password, verify_password
from whatsapp_service import (
    UNSUPPORTED_MESSAGE_REPLY,
    send_whatsapp_image,
    send_whatsapp_text,
)
from shopify_catalog_service import (
    ShopifyCatalogError, bicycle_collection, parse_search_intent,
    search_bicycles_by_collection, search_products,
)
from bicycle_recommendation import (
    asks_about_fit,
    available_bicycle_matches,
    detect_bicycle_preference,
    extract_child_age,
    format_bicycle_product,
    format_bicycle_results,
    has_available_variant,
    infer_guided_state,
    is_bicycle_request,
    is_bicycle_results_follow_up,
    is_generic_bicycle_request,
    recommended_bicycle_size,
)

logger = logging.getLogger(__name__)

# ---------------------- App & Middleware ----------------------
app = FastAPI(title=settings.APP_NAME)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    same_site="lax",
    https_only=True,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ---------------------- DB Session ----------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------------------- Helpers ----------------------
LOGIN_PATH = "/login"


LEAD_STATUSES = [
    "NEW",
    "REPLIED",
    "INTERESTED",
    "FOLLOW_UP",
    "NEGOTIATING",
    "PAYMENT_PENDING",
    "ORDER_CONFIRMED",
    "DELIVERED",
    "LOST",
]

HUMAN_HANDOVER_REPLY = (
    "Certainly. I'll leave this conversation for the PixiePinks team to assist you."
)
HUMAN_HANDOVER_PHRASES = (
    "human", "agent", "person", "staff", "customer service", "call me",
    "talk to someone", "representative", "speak to a person", "මනුස්සයෙක්",
    "කෙනෙක් එක්ක කතා", "සේවකයෙක්", "නියෝජිතයෙක්", "මට කතා කරන්න",
)
SHOPIFY_FALLBACK_REPLY = (
    "I’m unable to check our live product catalogue right now. "
    "Our team can assist you shortly."
)
PRODUCT_TERMS = (
    "product", "bicycle", "bike", "bag", "toy", "chocolate", "stationery", "gift",
    "price", "cost", "stock", "available", "availability", "size", "colour", "color",
    "variant", "brand", "model", "under rs", "below", "lkr", "how much", "recommend",
    "බයිසික", "මිල", "තියෙනවද", "පාට", "ප්‍රමාණ", "රු ", "ට අඩු",
)
FOLLOW_UP_TERMS = ("this", "that", "one", "second", "pink ones", "it", "මේ", "ඒක")


def needs_product_catalog(message: str, recent_context: list | None = None) -> bool:
    normalized = " ".join(message.casefold().split())
    if (any(term in normalized for term in PRODUCT_TERMS)
            or normalized.startswith(("do you have ", "have you got ", "show me "))):
        return True
    if any(term in normalized for term in FOLLOW_UP_TERMS):
        context = " ".join(item.get("message_text", "") for item in (recent_context or [])[-4:]).casefold()
        return any(term in context for term in PRODUCT_TERMS)
    return False


def requests_human_handover(message: str) -> bool:
    normalized = " ".join(message.casefold().split())
    return any(
        phrase in normalized
        if any(ord(character) > 127 for character in phrase)
        else re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized) is not None
        for phrase in HUMAN_HANDOVER_PHRASES
    )


def _store_conversation_message(
    phone_number: str,
    direction: str,
    message_text: str,
    *,
    customer_name: str | None = None,
    whatsapp_message_id: str | None = None,
    response_kind: str | None = None,
    created_at: datetime | None = None,
) -> None:
    try:
        with SessionLocal() as db:
            db.add(WhatsAppConversationMessage(
                phone_number=phone_number,
                customer_name=customer_name,
                direction=direction,
                message_text=message_text,
                whatsapp_message_id=whatsapp_message_id,
                response_kind=response_kind,
                created_at=created_at or datetime.utcnow(),
            ))
            db.commit()
    except SQLAlchemyError:
        logger.exception("Could not store WhatsApp conversation message direction=%s", direction)


def _reply_to_text_message(
    sender: str, message_id: str, text_body: str, profile_name: str | None
) -> None:
    """Generate and send after Meta has already received its HTTP 200 acknowledgement."""
    bicycle_delivery = None
    if requests_human_handover(text_body):
        reply = HUMAN_HANDOVER_REPLY
        response_kind = "handover"
        logger.info("WhatsApp human handover requested message_id=%s sender=%s", message_id, sender)
    else:
        try:
            with SessionLocal() as db:
                history = (
                    db.query(WhatsAppConversationMessage)
                    .filter(
                        WhatsAppConversationMessage.phone_number == sender,
                        or_(
                            WhatsAppConversationMessage.whatsapp_message_id.is_(None),
                            WhatsAppConversationMessage.whatsapp_message_id != message_id,
                        ),
                    )
                    .order_by(WhatsAppConversationMessage.created_at.desc())
                    .limit(8)
                    .all()
                )
            context = [
                {"direction": item.direction, "message_text": item.message_text}
                for item in reversed(history)
            ]
            preference, age, bicycle_stage = infer_guided_state(context)
            explicit_preference = detect_bicycle_preference(text_body)
            explicit_age = extract_child_age(text_body, standalone=bicycle_stage == "age")
            intent = parse_search_intent(text_body)
            direct_size = intent["filters"]["size"]
            had_bicycle_context = bicycle_stage in {"gender", "age", "results"}

            # Parse the current message before consulting history. A detail-free
            # bicycle enquiry after completed results begins a fresh guided flow;
            # result modifiers are the only messages that inherit completed state.
            generic_new_request = is_generic_bicycle_request(text_body)
            result_follow_up = (bicycle_stage == "results"
                                and is_bicycle_results_follow_up(text_body))
            if bicycle_stage == "results" and generic_new_request:
                preference, age, bicycle_stage = None, None, None
            elif explicit_preference and (explicit_age is not None or direct_size):
                # A self-contained demographic/size request replaces a completed
                # recommendation and must not be handled as "show more".
                preference, age, bicycle_stage = None, None, None
            elif (is_bicycle_request(text_body) and explicit_preference
                  and explicit_age is None and not direct_size and not result_follow_up):
                # "I want a bicycle for a boy" is a new request, not a request
                # to apply the old child's age to a different child.
                age = None
            preference = explicit_preference or preference
            age = explicit_age if explicit_age is not None else age
            if direct_size and explicit_preference:
                age = explicit_age  # retain only age stated in this same message
            bicycle_related = is_bicycle_request(text_body) or had_bicycle_context or bicycle_stage in {
                "gender", "age", "results"
            }

            if bicycle_related and asks_about_fit(text_body):
                reply = ("Age provides only a starting point for bicycle sizing; it cannot "
                         "guarantee the fit. The child's height and inseam can help confirm "
                         "the best size.")
            elif bicycle_related and not direct_size and preference is None:
                reply = "Yes, we do 🚲 Is the bicycle for a boy or a girl?"
            elif bicycle_related and not direct_size and age is None:
                pronoun = "he" if preference == "boy" else "she"
                reply = f"Great. How old is {pronoun}?"
            elif bicycle_related and preference and (age is not None or direct_size):
                size = int(direct_size) if direct_size else recommended_bicycle_size(age)
                collection_config = bicycle_collection(preference, size)
                if collection_config is None:
                    reply = (f"A {size}-inch bicycle is an age-based starting recommendation, "
                             f"but we do not have a verified {preference} {size}-inch collection. "
                             "I won't substitute another size or category. Would you like our "
                             "team to help confirm a suitable catalogue option?")
                    products_result = None
                else:
                    products_result = True
                search_query = text_body if (direct_size or bicycle_stage == "results") else ""
                try:
                    result = (search_bicycles_by_collection(preference, size, search_query, limit=10)
                              if products_result else None)
                except ShopifyCatalogError:
                    logger.warning("Live Shopify catalogue unavailable message_id=%s", message_id)
                    reply = SHOPIFY_FALLBACK_REPLY
                else:
                    if result is None:
                        products = []
                    elif result["collection"] is None:
                        reply = (f"I couldn't verify the {preference}s size {size}-inch Shopify "
                                 "collection, so I won't send potentially mismatched bicycles. "
                                 "Our team can check this category for you.")
                        products = []
                    else:
                        products = result["products"]
                    matches = available_bicycle_matches(
                        products, preference, limit=5 if bicycle_stage == "results" else 3,
                        cheapest="cheaper" in text_body.casefold(),
                    )
                    if bicycle_stage == "results":
                        old_text = " ".join(item["message_text"] for item in context).casefold()
                        unseen = [product for product in matches
                                  if str(product.get("url") or product.get("title", "")).casefold()
                                  not in old_text]
                        matches = unseen or matches
                    intro = ((f"For a {age}-year-old {preference}, a {size}-inch bicycle is "
                              "usually a good starting point. 🚲") if age is not None else
                             f"🚲 {preference.title()}s Size {size}\"")
                    if matches and has_available_variant(matches):
                        matches = [product for product in matches
                                   if has_available_variant([product])]
                        intro += "\nHere are some available options from our live Shopify collection:"
                        bicycle_delivery = (intro, matches[:3])
                        reply = intro
                    elif result and result.get("collection") and products:
                        reply = (intro + f"\n\nWe have {size}-inch {preference}s bicycles listed, "
                                 "but the matching options are currently shown as unavailable "
                                 "in our live catalogue.")
                    elif result and result.get("collection"):
                        reply = (format_bicycle_results(age, preference, size, matches)
                                 if age is not None else
                                 f"I couldn't find matching available bicycles in the verified "
                                 f"{preference}s size {size}-inch collection right now.")
            elif needs_product_catalog(text_body, context):
                prior_inbound = next((item["message_text"] for item in reversed(context)
                                      if item["direction"] == "inbound"), "")
                # Prior context is useful only for true follow-ups. Adding it to a
                # complete request introduces unrelated mandatory Shopify terms.
                search_query = text_body
                if not parse_search_intent(text_body)["terms"] and prior_inbound:
                    search_query = f"{prior_inbound} {text_body}"
                try:
                    catalog_results = search_products(search_query)
                except ShopifyCatalogError:
                    logger.warning("Live Shopify catalogue unavailable message_id=%s", message_id)
                    reply = SHOPIFY_FALLBACK_REPLY
                else:
                    reply = generate_customer_reply(
                        text_body, profile_name, context, catalog_results
                    )
            else:
                reply = generate_customer_reply(text_body, profile_name, context)
        except Exception:
            logger.exception("Unexpected AI integration failure message_id=%s", message_id)
            reply = FALLBACK_REPLY
        response_kind = "fallback" if reply in (FALLBACK_REPLY, SHOPIFY_FALLBACK_REPLY) else "ai"
        logger.info("WhatsApp reply path=%s message_id=%s sender=%s", response_kind, message_id, sender)

    if bicycle_delivery:
        intro, products = bicycle_delivery
        sent = send_whatsapp_text(sender, intro)
        _store_conversation_message(sender, "outbound", intro, customer_name=profile_name,
                                    response_kind="bicycle")
        for product in products:
            product_text = format_bicycle_product(product)
            image_url = product.get("featured_image")
            image_sent = False
            if product.get("featured_image_verified") and image_url:
                try:
                    image_sent = send_whatsapp_image(sender, image_url, product_text)
                except Exception:
                    # Outbound media must never turn Meta's webhook retry into duplicate batches.
                    logger.exception("Unexpected WhatsApp image failure message_id=%s sender=%s",
                                     message_id, sender)
            if not image_sent:
                send_whatsapp_text(sender, product_text)
            _store_conversation_message(sender, "outbound", product_text,
                                        customer_name=profile_name, response_kind="bicycle_product")
        follow_up = "Would you like more options, or to filter by colour, brand or budget?"
        send_whatsapp_text(sender, follow_up)
        _store_conversation_message(sender, "outbound", follow_up,
                                    customer_name=profile_name, response_kind="bicycle")
        logger.info("WhatsApp bicycle results sent count=%s message_id=%s sender=%s",
                    len(products), message_id, sender)
        return
    sent = send_whatsapp_text(sender, reply)
    logger.info("WhatsApp outbound result=%s message_id=%s sender=%s", sent, message_id, sender)
    _store_conversation_message(
        sender, "outbound", reply, customer_name=profile_name, response_kind=response_kind
    )


def _send_unsupported_reply(sender: str, profile_name: str | None) -> None:
    sent = send_whatsapp_text(sender, UNSUPPORTED_MESSAGE_REPLY)
    logger.info("WhatsApp unsupported-message outbound result=%s sender=%s", sent, sender)
    _store_conversation_message(
        sender,
        "outbound",
        UNSUPPORTED_MESSAGE_REPLY,
        customer_name=profile_name,
        response_kind="unsupported",
    )


# ---------------------- Meta WhatsApp Webhook ----------------------
@app.get("/webhook")
def verify_meta_webhook(
    mode: str | None = Query(default=None, alias="hub.mode"),
    verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    if (
        mode == "subscribe"
        and verify_token == settings.META_VERIFY_TOKEN
        and challenge is not None
    ):
        return Response(content=challenge, media_type="text/plain", status_code=200)
    raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhook")
async def receive_meta_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    if settings.META_APP_SECRET:
        supplied_signature = request.headers.get("X-Hub-Signature-256", "")
        expected_signature = "sha256=" + hmac.new(
            settings.META_APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            logger.warning("Rejected WhatsApp webhook with an invalid signature")
            raise HTTPException(status_code=403, detail="Invalid webhook signature")
    else:
        logger.warning("META_APP_SECRET is not configured; webhook signature was not verified")

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Received a Meta webhook request with invalid JSON")
        return {"status": "ok"}

    if not isinstance(payload, dict):
        logger.warning("Ignored WhatsApp webhook whose JSON root is not an object")
        return {"status": "ok"}

    entries = payload.get("entry")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        for change in changes if isinstance(changes, list) else []:
            if not isinstance(change, dict) or change.get("field") not in (None, "messages"):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue

            contacts = value.get("contacts")
            profile_names = {
                contact.get("wa_id"): contact.get("profile", {}).get("name")
                for contact in (contacts if isinstance(contacts, list) else [])
                if isinstance(contact, dict) and isinstance(contact.get("profile"), dict)
            }
            messages = value.get("messages", [])
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                message_id = message.get("id")
                sender = message.get("from")
                message_type = message.get("type")
                if not all(isinstance(item, str) and item for item in (message_id, sender, message_type)):
                    logger.warning("Ignored malformed WhatsApp message object")
                    continue

                try:
                    received_at = datetime.utcfromtimestamp(int(message.get("timestamp")))
                except (TypeError, ValueError, OSError, OverflowError):
                    received_at = datetime.utcnow()

                record = ProcessedWhatsAppMessage(
                    message_id=message_id,
                    sender_phone=sender,
                    message_type=message_type,
                    received_at=received_at,
                    processed_at=datetime.utcnow(),
                )
                with SessionLocal() as db:
                    try:
                        db.add(record)
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                        logger.info("Ignored duplicate WhatsApp message message_id=%s", message_id)
                        continue
                    except SQLAlchemyError:
                        db.rollback()
                        logger.exception("Could not record WhatsApp message message_id=%s", message_id)
                        continue

                profile_name = profile_names.get(sender)
                text_body = None
                if message_type == "text" and isinstance(message.get("text"), dict):
                    text_body = message["text"].get("body")
                logger.info(
                    "WhatsApp inbound message_id=%s sender=%s type=%s profile_name=%s",
                    message_id,
                    sender,
                    message_type,
                    profile_name,
                )
                if message_type == "text" and isinstance(text_body, str):
                    cleaned_body = text_body.strip()[:2_000]
                    if not cleaned_body:
                        logger.info("Ignored empty WhatsApp text message message_id=%s", message_id)
                        continue
                    _store_conversation_message(
                        sender,
                        "inbound",
                        cleaned_body,
                        customer_name=profile_name,
                        whatsapp_message_id=message_id,
                        created_at=received_at,
                    )
                    background_tasks.add_task(
                        _reply_to_text_message,
                        sender,
                        message_id,
                        cleaned_body,
                        profile_name,
                    )
                elif message_type in {
                    "image", "audio", "video", "document", "sticker", "location",
                    "contacts", "reaction", "interactive",
                }:
                    background_tasks.add_task(
                        _send_unsupported_reply, sender, profile_name
                    )

    return {"status": "ok"}


def _generate_lead_no(db: Session, dt: date | None = None) -> str:
    dt = dt or date.today()
    year = dt.year
    prefix = f"LD-{year}-"
    latest = (
        db.query(Lead)
        .filter(Lead.lead_no.like(f"{prefix}%"))
        .order_by(Lead.id.desc())
        .first()
    )
    if latest and latest.lead_no:
        try:
            seq = int(latest.lead_no.split("-")[-1]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:05d}"


def _is_safe_next(next_url: str) -> bool:
    try:
        u = urlparse(next_url)
        return (not u.netloc) and next_url.startswith("/")
    except Exception:
        return False

def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    if uid:
        user = db.get(User, uid)
        if user:
            return user
        request.session.clear()
    raise HTTPException(
        status_code=303,
        headers={"Location": f"{LOGIN_PATH}?next={quote(request.url.path)}"}
    )

# ---------------------- Startup ----------------------
@app.on_event("startup")
def startup():
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        Base.metadata.create_all(bind=engine)
    else:
        Base.metadata.create_all(bind=engine)

    ensure_item_sku_column()
    init_db()

    with SessionLocal() as db:
        if not db.query(User).filter_by(username="admin").first():
            db.add(User(username="admin", password_hash=hash_password("change-me")))
            db.commit()


def ensure_item_sku_column():
    inspector = inspect(engine)
    item_columns = {col["name"] for col in inspector.get_columns("items")}

    if "sku" not in item_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE items ADD COLUMN sku VARCHAR"))

    with SessionLocal() as db:
        existing_skus = {
            sku for (sku,) in db.query(Item.sku).filter(Item.sku.isnot(None), Item.sku != "").all()
        }
        missing_items = (
            db.query(Item)
            .filter(Item.sku.is_(None) | (Item.sku == ""))
            .order_by(Item.id)
            .all()
        )

        for item in missing_items:
            base_sku = f"ITEM-{item.id:04d}"
            candidate = base_sku
            suffix = 1
            while candidate in existing_skus:
                suffix += 1
                candidate = f"{base_sku}-{suffix}"
            item.sku = candidate
            existing_skus.add(candidate)

        if missing_items:
            db.commit()

    if settings.DATABASE_URL.startswith("sqlite"):
        with engine.begin() as conn:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_items_sku ON items (sku)"))

# ---------------------- Auth Routes ----------------------
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str | None = "/"):
    return templates.TemplateResponse("login.html", {"request": request, "next": next})

@app.post("/login")
def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard"),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    if user and verify_password(password, user.password_hash):
        request.session["uid"] = user.id
        request.session["user"] = {"username": user.username}
        if not _is_safe_next(next):
            next = "/"
        return RedirectResponse(next, status_code=303)
    return RedirectResponse(f"{LOGIN_PATH}?error=Invalid&next={quote(next)}", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(LOGIN_PATH, status_code=303)

# ---------------------- Protected Pages ----------------------
@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    if request.session.get("uid"):
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/login", status_code=303)

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    today = date.today()
    start_month = today.replace(day=1)

    revenue = (
        db.query(func.coalesce(func.sum(JournalLine.credit), 0))
        .join(Account).filter(Account.type == "INCOME")
        .join(JournalEntry).filter(JournalEntry.date >= start_month, JournalEntry.date <= today)
        .scalar() or 0
    )
    expenses = (
        db.query(func.coalesce(func.sum(JournalLine.debit), 0))
        .join(Account).filter(Account.type == "EXPENSE")
        .join(JournalEntry).filter(JournalEntry.date >= start_month, JournalEntry.date <= today)
        .scalar() or 0
    )
    profit = float(revenue) - float(expenses)

    cash_acc = db.query(Account).filter(Account.name.in_(["Cash on Hand", "Bank - Current Account"])).all()
    cash_balance = 0.0
    for acc in cash_acc:
        dr = db.query(func.coalesce(func.sum(JournalLine.debit), 0)).filter(JournalLine.account_id == acc.id).scalar() or 0
        cr = db.query(func.coalesce(func.sum(JournalLine.credit), 0)).filter(JournalLine.account_id == acc.id).scalar() or 0
        cash_balance += float(dr) - float(cr)

    return templates.TemplateResponse("dashboard.html", {
        "request": request, "currency": settings.CURRENCY,
        "revenue": revenue, "expenses": expenses, "profit": profit,
        "cash_balance": cash_balance
    })

@app.get("/accounts", response_class=HTMLResponse)
def list_accounts(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    accounts = db.query(Account).order_by(Account.code).all()
    return templates.TemplateResponse("accounts.html", {"request": request, "accounts": accounts})

@app.post("/accounts")
def create_account(
    code: str = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    subtype: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_db)
):
    acc = Account(
        code=code.strip(),
        name=name.strip(),
        type=type.strip().upper(),
        subtype=subtype.strip(),
        description=description.strip()
    )
    db.add(acc)
    db.commit()
    return RedirectResponse("/accounts", status_code=303)

# ---------------------- Customers ----------------------
@app.get("/customers", response_class=HTMLResponse)
def list_customers(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    customers = db.query(Customer).order_by(Customer.name).all()
    return templates.TemplateResponse("customers.html", {"request": request, "customers": customers})

@app.post("/customers")
def create_customer(name: str = Form(...), email: str = Form(""), phone: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_user)):
    c = Customer(name=name.strip(), email=email.strip(), phone=phone.strip())
    db.add(c)
    db.commit()
    return RedirectResponse("/customers", status_code=303)

@app.post("/customers/{cust_id}/delete")
def delete_customer(cust_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    c = db.get(Customer, cust_id)
    if not c:
        raise HTTPException(status_code=404, detail="Customer not found")
    db.delete(c)
    db.commit()
    return RedirectResponse("/customers", status_code=303)

# ---------------------- Suppliers ----------------------
@app.get("/suppliers", response_class=HTMLResponse)
def list_suppliers(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    suppliers = db.query(Supplier).order_by(Supplier.name).all()
    return templates.TemplateResponse("suppliers.html", {"request": request, "suppliers": suppliers})

@app.post("/suppliers")
def create_supplier(name: str = Form(...), email: str = Form(""), phone: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_user)):
    s = Supplier(name=name.strip(), email=email.strip(), phone=phone.strip())
    db.add(s)
    db.commit()
    return RedirectResponse("/suppliers", status_code=303)

@app.post("/suppliers/{sup_id}/delete")
def delete_supplier(sup_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    s = db.get(Supplier, sup_id)
    if not s:
        raise HTTPException(status_code=404, detail="Supplier not found")
    db.delete(s)
    db.commit()
    return RedirectResponse("/suppliers", status_code=303)

# ---------------------- Items ----------------------
@app.get("/items", response_class=HTMLResponse)
def list_items(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    items = db.query(Item).order_by(Item.sku, Item.name).all()
    return templates.TemplateResponse("items.html", {"request": request, "items": items})

@app.post("/items")
def create_item(
    name: str = Form(...),
    sku: str = Form(...),
    unit: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    i = Item(name=name.strip(), sku=sku.strip(), unit=unit.strip())
    db.add(i)
    db.commit()
    return RedirectResponse("/items", status_code=303)

@app.post("/items/{item_id}/delete")
def delete_item(item_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    i = db.get(Item, item_id)
    if not i:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(i)
    db.commit()
    return RedirectResponse("/items", status_code=303)


# ---------------------- CRM ----------------------
@app.get("/crm", response_class=HTMLResponse)
def crm_dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    today = date.today()
    total_leads = db.query(func.count(Lead.id)).scalar() or 0
    new_leads = db.query(func.count(Lead.id)).filter(Lead.status == "NEW").scalar() or 0
    followups_today = db.query(func.count(Lead.id)).filter(Lead.next_followup == today).scalar() or 0
    completed_sales = db.query(func.count(Lead.id)).filter(Lead.status == "DELIVERED").scalar() or 0
    lost_leads = db.query(func.count(Lead.id)).filter(Lead.status == "LOST").scalar() or 0

    pending_followups = (
        db.query(Lead)
        .filter(Lead.next_followup.is_not(None), Lead.next_followup <= today, Lead.status.notin_(["DELIVERED", "LOST"]))
        .order_by(Lead.next_followup.asc(), Lead.created_at.desc())
        .all()
    )

    recent_leads = db.query(Lead).order_by(Lead.created_at.desc(), Lead.id.desc()).limit(10).all()

    return templates.TemplateResponse("crm_dashboard.html", {
        "request": request,
        "total_leads": total_leads,
        "new_leads": new_leads,
        "followups_today": followups_today,
        "completed_sales": completed_sales,
        "lost_leads": lost_leads,
        "recent_leads": recent_leads,
        "pending_followups": pending_followups,
    })


@app.get("/crm/leads", response_class=HTMLResponse)
def crm_leads(
    request: Request,
    query: str = "",
    range_type: str = "this_week",
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    lead_query = db.query(Lead)
    today = date.today()

    selected_range = (range_type or "this_week").strip().lower()
    start_date = today - timedelta(days=today.weekday())
    end_date = today

    if selected_range == "today":
        start_date = today
        end_date = today
    elif selected_range == "yesterday":
        start_date = today - timedelta(days=1)
        end_date = today - timedelta(days=1)
    elif selected_range == "day_before_yesterday":
        start_date = today - timedelta(days=2)
        end_date = today - timedelta(days=2)
    elif selected_range == "this_month":
        start_date = today.replace(day=1)
    elif selected_range == "last_week":
        current_week_start = today - timedelta(days=today.weekday())
        start_date = current_week_start - timedelta(days=7)
        end_date = current_week_start - timedelta(days=1)
    elif selected_range == "last_month":
        first_day_this_month = today.replace(day=1)
        end_date = first_day_this_month - timedelta(days=1)
        start_date = end_date.replace(day=1)
    elif selected_range == "this_year":
        start_date = date(today.year, 1, 1)
    elif selected_range == "last_year":
        start_date = date(today.year - 1, 1, 1)
        end_date = date(today.year - 1, 12, 31)
    elif selected_range == "this_quarter":
        quarter_start_month = ((today.month - 1) // 3) * 3 + 1
        start_date = date(today.year, quarter_start_month, 1)
    elif selected_range == "last_quarter":
        current_quarter = (today.month - 1) // 3 + 1
        if current_quarter == 1:
            last_quarter_year = today.year - 1
            last_quarter_start_month = 10
        else:
            last_quarter_year = today.year
            last_quarter_start_month = (current_quarter - 2) * 3 + 1
        start_date = date(last_quarter_year, last_quarter_start_month, 1)
        if last_quarter_start_month == 10:
            end_date = date(last_quarter_year, 12, 31)
        else:
            next_quarter_start = date(last_quarter_year, last_quarter_start_month + 3, 1)
            end_date = next_quarter_start - timedelta(days=1)
    elif selected_range == "custom":
        parsed_from = None
        parsed_to = None
        try:
            parsed_from = datetime.strptime(date_from, "%Y-%m-%d").date() if date_from else None
        except ValueError:
            parsed_from = None
        try:
            parsed_to = datetime.strptime(date_to, "%Y-%m-%d").date() if date_to else None
        except ValueError:
            parsed_to = None

        if parsed_from and parsed_to:
            start_date, end_date = sorted([parsed_from, parsed_to])
        elif parsed_from:
            start_date, end_date = parsed_from, today
        elif parsed_to:
            start_date, end_date = parsed_to, parsed_to
    else:
        selected_range = "this_week"

    lead_query = lead_query.filter(func.date(Lead.created_at).between(start_date, end_date))

    search_query = (query or "").strip()
    if search_query:
        query_filter = f"%{search_query}%"
        lead_query = lead_query.filter(
            or_(
                Lead.customer_name.ilike(query_filter),
                Lead.mobile.ilike(query_filter),
                Lead.city.ilike(query_filter),
                Lead.product_interest.ilike(query_filter),
                Lead.lead_no.ilike(query_filter),
                Lead.assigned_to.ilike(query_filter),
                Lead.notes.ilike(query_filter),
            )
        )

    leads = lead_query.order_by(Lead.created_at.desc(), Lead.id.desc()).all()
    crm_users = db.query(CRMUser).order_by(CRMUser.name).all()
    return templates.TemplateResponse(
        "crm_leads.html",
        {
            "request": request,
            "leads": leads,
            "statuses": LEAD_STATUSES,
            "crm_users": crm_users,
            "query": search_query,
            "range_type": selected_range,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


@app.post("/crm/leads")
def crm_create_lead(
    customer_name: str = Form(...),
    mobile: str = Form(...),
    city: str = Form(""),
    product_interest: str = Form(""),
    status: str = Form("NEW"),
    assigned_to: str = Form(""),
    new_assignee: str = Form(""),
    notes: str = Form(""),
    next_followup: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    selected_assigned_to = assigned_to.strip()
    new_assignee_name = new_assignee.strip()

    if new_assignee_name:
        existing_user = db.query(CRMUser).filter(func.lower(CRMUser.name) == new_assignee_name.lower()).first()
        if not existing_user:
            db.add(CRMUser(name=new_assignee_name))
        selected_assigned_to = new_assignee_name
    elif selected_assigned_to == "__new__":
        selected_assigned_to = ""

    lead = Lead(
        lead_no=_generate_lead_no(db),
        customer_name=customer_name.strip(),
        mobile=mobile.strip(),
        city=city.strip(),
        product_interest=product_interest.strip(),
        status=status if status in LEAD_STATUSES else "NEW",
        assigned_to=selected_assigned_to,
        notes=notes.strip(),
        next_followup=datetime.strptime(next_followup, "%Y-%m-%d").date() if next_followup else None,
    )
    db.add(lead)
    db.commit()
    return RedirectResponse("/crm/leads", status_code=303)


@app.get("/crm/leads/{lead_id}", response_class=HTMLResponse)
def crm_lead_view(lead_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    notes = db.query(LeadNote).filter(LeadNote.lead_id == lead_id).order_by(LeadNote.created_at.desc()).all()
    tasks = db.query(LeadTask).filter(LeadTask.lead_id == lead_id).order_by(LeadTask.task_date.desc(), LeadTask.id.desc()).all()

    return templates.TemplateResponse("crm_lead_view.html", {
        "request": request, "lead": lead, "notes": notes, "tasks": tasks, "statuses": LEAD_STATUSES
    })


@app.post("/crm/leads/{lead_id}/note")
def crm_add_note(lead_id: int, note: str = Form(...), db: Session = Depends(get_db), user: User = Depends(require_user)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    db.add(LeadNote(lead_id=lead_id, note=note.strip()))
    db.commit()
    return RedirectResponse(f"/crm/leads/{lead_id}", status_code=303)


@app.post("/crm/leads/{lead_id}/followup")
def crm_add_followup(
    lead_id: int,
    task_date: str = Form(...),
    status: str = Form("PENDING"),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    parsed_date = datetime.strptime(task_date, "%Y-%m-%d").date()
    task = LeadTask(lead_id=lead_id, task_date=parsed_date, status=status.strip().upper(), note=note.strip())
    lead.next_followup = parsed_date
    if status in LEAD_STATUSES:
        lead.status = status
    db.add(task)
    db.commit()
    return RedirectResponse(f"/crm/leads/{lead_id}", status_code=303)


# ---------------------- Entries ----------------------
@app.get("/entries", response_class=HTMLResponse)
def list_entries(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    entries = db.query(JournalEntry).order_by(JournalEntry.date.desc(), JournalEntry.id.desc()).limit(200).all()
    accounts = db.query(Account).order_by(Account.code).all()
    customers = db.query(Customer).order_by(Customer.name).all()
    suppliers = db.query(Supplier).order_by(Supplier.name).all()
    items = db.query(Item).order_by(Item.name).all()
    return templates.TemplateResponse(
        "entries.html",
        {
            "request": request,
            "entries": entries,
            "accounts": accounts,
            "customers": customers,
            "suppliers": suppliers,
            "items": items,
            "currency": settings.CURRENCY,
        },
    )

@app.post("/entries")
def create_entry(
    date_str: str = Form(...),
    memo: str = Form(""),
    accounts: list[int] = Form(...),
    descriptions: list[str] = Form(...),
    debits: list[str] = Form(...),
    credits: list[str] = Form(...),
    party_types: list[str] = Form(...),
    party_ids: list[str] = Form(...),
    qtys: list[str] = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    entry = JournalEntry(date=dt, memo=memo)
    db.add(entry)
    db.flush()

    total_debit = 0.0
    total_credit = 0.0
    for a, d, dr, cr, pt, pid, q in zip(accounts, descriptions, debits, credits, party_types, party_ids, qtys):
        dr_amt = float(dr or 0)
        cr_amt = float(cr or 0)
        total_debit += dr_amt
        total_credit += cr_amt

        line = JournalLine(
            entry_id=entry.id,
            account_id=int(a),
            description=d.strip() if d else "",
            debit=dr_amt,
            credit=cr_amt,
            party_type=pt or None,
            party_id=int(pid) if pid else None,
            qty=float(q or 0)
        )
        db.add(line)

    if round(total_debit, 2) != round(total_credit, 2):
        db.rollback()
        return RedirectResponse("/entries?error=Not%20balanced", status_code=303)

    db.commit()
    return RedirectResponse("/entries", status_code=303)

@app.post("/entries/{entry_id}/delete")
@app.get("/entries/{entry_id}/delete", include_in_schema=False)
def delete_entry(entry_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    entry = db.get(JournalEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    db.delete(entry)
    db.commit()
    return RedirectResponse(url="/entries", status_code=status.HTTP_303_SEE_OTHER)

# ---------------------- Reports ----------------------
# (Trial Balance, Income Statement, Balance Sheet remain same as your version)
@app.get("/reports/trial-balance", response_class=HTMLResponse)
def trial_balance(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    from datetime import datetime as dt
    start_dt = dt.strptime(start, "%Y-%m-%d").date() if start else None
    end_dt = dt.strptime(end, "%Y-%m-%d").date() if end else None

    accounts = db.query(Account).order_by(Account.code).all()
    rows = []
    total_debit = 0.0
    total_credit = 0.0

    for acc in accounts:
        dr = db.query(func.coalesce(func.sum(JournalLine.debit), 0)).join(JournalEntry).filter(JournalLine.account_id == acc.id)
        cr = db.query(func.coalesce(func.sum(JournalLine.credit), 0)).join(JournalEntry).filter(JournalLine.account_id == acc.id)
        if start_dt:
            dr = dr.filter(JournalEntry.date >= start_dt)
            cr = cr.filter(JournalEntry.date >= start_dt)
        if end_dt:
            dr = dr.filter(JournalEntry.date <= end_dt)
            cr = cr.filter(JournalEntry.date <= end_dt)
        dr_amt = float(dr.scalar() or 0)
        cr_amt = float(cr.scalar() or 0)
        bal = dr_amt - cr_amt
        debit = bal if bal > 0 else 0.0
        credit = -bal if bal < 0 else 0.0
        total_debit += debit
        total_credit += credit
        rows.append({"code": acc.code, "name": acc.name, "debit": debit, "credit": credit})

    return templates.TemplateResponse(
        "trial_balance.html",
        {
            "request": request,
            "rows": rows,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "currency": settings.CURRENCY,
            "start": start,
            "end": end,
        },
    )

@app.get("/reports/income-statement", response_class=HTMLResponse)
def income_statement(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    from datetime import datetime as dt
    if not start or not end:
        return templates.TemplateResponse(
            "income_statement.html",
            {
                "request": request,
                "currency": settings.CURRENCY,
                "start": start,
                "end": end,
                "income": 0,
                "cogs": 0,
                "other_exp": 0,
                "gross_profit": 0,
                "net_profit": 0,
            },
        )

    start_dt = dt.strptime(start, "%Y-%m-%d").date()
    end_dt = dt.strptime(end, "%Y-%m-%d").date()

    income = (
        db.query(func.coalesce(func.sum(JournalLine.credit), 0))
        .join(Account).filter(Account.type == "INCOME")
        .join(JournalEntry)
        .filter(JournalEntry.date >= start_dt, JournalEntry.date <= end_dt)
        .scalar() or 0
    )
    cogs = (
        db.query(func.coalesce(func.sum(JournalLine.debit), 0))
        .join(Account).filter(Account.code == "5000")
        .join(JournalEntry)
        .filter(JournalEntry.date >= start_dt, JournalEntry.date <= end_dt)
        .scalar() or 0
    )
    other_exp = (
        db.query(func.coalesce(func.sum(JournalLine.debit), 0))
        .join(Account).filter(Account.type == "EXPENSE", Account.code != "5000")
        .join(JournalEntry)
        .filter(JournalEntry.date >= start_dt, JournalEntry.date <= end_dt)
        .scalar() or 0
    )

    gross_profit = float(income) - float(cogs)
    net_profit = gross_profit - float(other_exp)

    return templates.TemplateResponse(
        "income_statement.html",
        {
            "request": request,
            "currency": settings.CURRENCY,
            "start": start,
            "end": end,
            "income": income,
            "cogs": cogs,
            "other_exp": other_exp,
            "gross_profit": gross_profit,
            "net_profit": net_profit,
        },
    )


# ---------------------- Balance Sheet ----------------------
@app.get("/reports/balance-sheet", response_class=HTMLResponse)
def balance_sheet(request: Request, as_of: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_user)):
    from datetime import datetime as dt

    if not as_of:
        return templates.TemplateResponse("balance_sheet.html", {
            "request": request, "currency": settings.CURRENCY, "as_of": None,
            "assets_current": [], "assets_non_current": [],
            "liab_current": [], "liab_non_current": [],
            "equity_capital": [], "retained_earnings": 0,
            "assets_total": 0, "liab_total": 0,
            "equity_total": 0, "liab_equity_total": 0
        })

    as_of_dt = dt.strptime(as_of, "%Y-%m-%d").date()

    def account_balance(acc: Account):
        dr = db.query(func.coalesce(func.sum(JournalLine.debit), 0))\
            .filter(JournalLine.account_id == acc.id).join(JournalEntry).filter(JournalEntry.date <= as_of_dt).scalar() or 0
        cr = db.query(func.coalesce(func.sum(JournalLine.credit), 0))\
            .filter(JournalLine.account_id == acc.id).join(JournalEntry).filter(JournalEntry.date <= as_of_dt).scalar() or 0
        if acc.type in {"ASSET", "EXPENSE"}:
            return float(dr) - float(cr)
        return float(cr) - float(dr)

    accounts = db.query(Account).all()

    assets_current, assets_non_current = [], []
    liab_current, liab_non_current = [], []
    equity_capital = []
    retained_earnings = 0

    for acc in accounts:
        bal = account_balance(acc)
        if abs(bal) < 0.01:
            continue
        if acc.type == "ASSET":
            if acc.subtype == "CURRENT_ASSET":
                assets_current.append((acc.name, bal))
            elif acc.subtype == "NON_CURRENT_ASSET":
                assets_non_current.append((acc.name, bal))
        elif acc.type == "LIABILITY":
            if acc.subtype == "CURRENT_LIABILITY":
                liab_current.append((acc.name, bal))
            elif acc.subtype == "NON_CURRENT_LIABILITY":
                liab_non_current.append((acc.name, bal))
        elif acc.type == "EQUITY":
            if acc.subtype == "CAPITAL":
                equity_capital.append((acc.name, bal))
            elif acc.subtype == "RETAINED_EARNINGS":
                retained_earnings += bal
        elif acc.type == "INCOME":
            retained_earnings += bal
        elif acc.type == "EXPENSE":
            retained_earnings -= bal

    assets_total = sum(b for _, b in assets_current + assets_non_current)
    liab_total = sum(b for _, b in liab_current + liab_non_current)
    eq_cap_total = sum(b for _, b in equity_capital)

    equity_total = eq_cap_total + retained_earnings
    liab_equity_total = liab_total + equity_total

    return templates.TemplateResponse("balance_sheet.html", {
        "request": request, "currency": settings.CURRENCY, "as_of": as_of,
        "assets_current": assets_current, "assets_non_current": assets_non_current,
        "liab_current": liab_current, "liab_non_current": liab_non_current,
        "equity_capital": equity_capital, "retained_earnings": retained_earnings,
        "assets_total": assets_total, "liab_total": liab_total,
        "equity_total": equity_total, "liab_equity_total": liab_equity_total
    })
