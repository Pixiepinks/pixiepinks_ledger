from sqlalchemy import Boolean, Column, Integer, String, Date, DateTime, ForeignKey, Numeric, Text, Float, Enum, JSON, UniqueConstraint
from sqlalchemy.orm import relationship, Mapped, mapped_column
from datetime import datetime
from database import Base
import enum

# ----------------------
# Party Type Enum
# ----------------------
class PartyType(str, enum.Enum):
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    ITEM = "ITEM"

# ----------------------
# Accounts & Journal
# ----------------------
class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # ASSET, LIABILITY, EQUITY, INCOME, EXPENSE
    subtype = Column(String, nullable=True)  # Current Asset, Non-Current Asset, etc.
    description = Column(String, nullable=True)

    lines = relationship("JournalLine", back_populates="account", cascade="all, delete-orphan")

class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    date: Mapped[datetime] = mapped_column(Date, index=True)
    memo: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    lines = relationship("JournalLine", back_populates="entry", cascade="all, delete-orphan")

class JournalLine(Base):
    __tablename__ = "journal_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(String(255), default="")
    debit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    credit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)

    # 🔹 New fields for Hybrid Sub-ledgers
    party_type = Column(Enum(PartyType), nullable=True)   # CUSTOMER / SUPPLIER / ITEM
    party_id = Column(Integer, nullable=True)             # Refers to Customer.id / Supplier.id / Item.id
    qty = Column(Float, default=0.0)                      # For inventory qty tracking

    entry = relationship("JournalEntry", back_populates="lines")
    account = relationship("Account", back_populates="lines")

# ----------------------
# Master Data Tables
# ----------------------
class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)

class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    sku = Column(String, nullable=False, unique=True, index=True)
    unit = Column(String, nullable=True)   # e.g., pcs, kg, box

# ----------------------
# Users
# ----------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(30), nullable=False, default="STAFF", server_default="STAFF", index=True)


# ---------------------- Commercial sales orders ----------------------
class SalesOrder(Base):
    __tablename__ = "sales_orders"
    id = Column(Integer, primary_key=True)
    internal_order_number = Column(String(40), unique=True, nullable=False, index=True)
    idempotency_key = Column(String(100), unique=True, nullable=False)
    crm_conversation_id = Column(Integer, ForeignKey("whatsapp_conversations.id"), index=True)
    crm_lead_id = Column(Integer, ForeignKey("leads.id"), index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), index=True)
    customer_name = Column(String(255), nullable=False)
    whatsapp_phone = Column(String(50), nullable=False, index=True)
    alternative_phone = Column(String(50))
    delivery_contact_person = Column(String(255))
    delivery_address = Column(Text)
    city = Column(String(120))
    district = Column(String(120))
    postal_code = Column(String(30))
    customer_notes = Column(Text)
    order_status = Column(String(40), nullable=False, default="DRAFT", index=True)
    payment_status = Column(String(40), nullable=False, default="UNPAID", index=True)
    fulfillment_status = Column(String(40), nullable=False, default="UNFULFILLED", index=True)
    shopify_sync_status = Column(String(40), nullable=False, default="NOT_SYNCED", index=True)
    shopify_draft_order_id = Column(String(255), unique=True)
    shopify_order_id = Column(String(255), unique=True)
    shopify_order_name = Column(String(100), index=True)
    subtotal = Column(Numeric(14, 2), nullable=False, default=0)
    discount_total = Column(Numeric(14, 2), nullable=False, default=0)
    shipping_total = Column(Numeric(14, 2), nullable=False, default=0)
    tax_total = Column(Numeric(14, 2), nullable=False, default=0)
    grand_total = Column(Numeric(14, 2), nullable=False, default=0)
    amount_paid = Column(Numeric(14, 2), nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="LKR")
    notes = Column(Text)
    created_by = Column(String(100), nullable=False, index=True)
    confirmed_by = Column(String(100))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    lines = relationship("SalesOrderLine", cascade="all, delete-orphan", back_populates="order")


class SalesOrderLine(Base):
    __tablename__ = "sales_order_lines"
    id = Column(Integer, primary_key=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    inventory_item_id = Column(Integer, ForeignKey("items.id"), index=True)
    shopify_product_id = Column(String(255), nullable=False, index=True)
    shopify_variant_id = Column(String(255), nullable=False, index=True)
    sku = Column(String(255), index=True)
    product_name_snapshot = Column(String(500), nullable=False)
    variant_name_snapshot = Column(String(500))
    product_image_url = Column(Text)
    product_url = Column(Text)
    quantity = Column(Numeric(14, 3), nullable=False)
    unit_price = Column(Numeric(14, 2), nullable=False)
    discount_amount = Column(Numeric(14, 2), nullable=False, default=0)
    tax_amount = Column(Numeric(14, 2), nullable=False, default=0)
    line_total = Column(Numeric(14, 2), nullable=False)
    unit_cost_snapshot = Column(Numeric(14, 2))
    order = relationship("SalesOrder", back_populates="lines")


class SalesPayment(Base):
    __tablename__ = "sales_payments"
    id = Column(Integer, primary_key=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_reference = Column(String(255), index=True)
    payment_method = Column(String(40), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    payment_date = Column(Date, nullable=False)
    verification_status = Column(String(40), nullable=False, default="PENDING", index=True)
    verified_by = Column(String(100))
    verified_at = Column(DateTime)
    accounting_entry_id = Column(Integer, ForeignKey("journal_entries.id"))
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SalesOrderStatusHistory(Base):
    __tablename__ = "sales_order_status_history"
    id = Column(Integer, primary_key=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    previous_status = Column(String(40), nullable=False)
    new_status = Column(String(40), nullable=False, index=True)
    changed_by = Column(String(100), nullable=False)
    reason = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class ShopifySyncEvent(Base):
    __tablename__ = "shopify_sync_events"
    id = Column(Integer, primary_key=True)
    event_id = Column(String(255), unique=True, index=True)
    event_type = Column(String(80), nullable=False, index=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id", ondelete="RESTRICT"), index=True)
    external_reference = Column(String(255), index=True)
    direction = Column(String(20), nullable=False)
    idempotency_key = Column(String(100), unique=True, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    status = Column(String(40), nullable=False, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    error_summary = Column(String(500))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    processed_at = Column(DateTime)


class AccountingPostingLink(Base):
    __tablename__ = "accounting_posting_links"
    __table_args__ = (UniqueConstraint("source_document_type", "source_document_id", "posting_event_type",
                                       name="uq_accounting_posting_source_event"),)
    id = Column(Integer, primary_key=True)
    source_document_type = Column(String(50), nullable=False)
    source_document_id = Column(Integer, nullable=False)
    posting_event_type = Column(String(50), nullable=False)
    journal_entry_id = Column(Integer, ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=False, unique=True)
    reversal_reference = Column(Integer, ForeignKey("accounting_posting_links.id"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ----------------------
# CRM
# ----------------------


class CRMUser(Base):
    __tablename__ = "crm_users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    lead_no = Column(String, unique=True, index=True)
    customer_name = Column(String, nullable=False)
    mobile = Column(String, nullable=False)
    city = Column(String)
    source = Column(String, default="WhatsApp")
    status = Column(String, default="NEW")
    assigned_to = Column(String)
    product_interest = Column(String)
    notes = Column(Text)
    next_followup = Column(Date)
    created_at = Column(DateTime, default=datetime.utcnow)


class LeadNote(Base):
    __tablename__ = "lead_notes"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"))
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class LeadTask(Base):
    __tablename__ = "lead_tasks"

    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"))
    task_date = Column(Date)
    status = Column(String, default="PENDING")
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


# ----------------------
# WhatsApp webhook idempotency
# ----------------------
class ProcessedWhatsAppMessage(Base):
    __tablename__ = "processed_whatsapp_messages"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(255), unique=True, nullable=False, index=True)
    sender_phone = Column(String(50), nullable=False)
    message_type = Column(String(50), nullable=False)
    received_at = Column(DateTime, nullable=False)
    processed_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WhatsAppConversationMessage(Base):
    """A small, provider-token-free history used for customer-service context."""

    __tablename__ = "whatsapp_conversation_messages"

    id = Column(Integer, primary_key=True)
    phone_number = Column(String(50), nullable=False, index=True)
    customer_name = Column(String(255), nullable=True)
    direction = Column(String(20), nullable=False)
    message_text = Column(Text, nullable=False)
    whatsapp_message_id = Column(String(255), nullable=True, index=True)
    response_kind = Column(String(20), nullable=True)
    send_status = Column(String(20), nullable=True)
    sent_by = Column(String(100), nullable=True)
    media_id = Column(String(255), nullable=True)
    media_mime_type = Column(String(100), nullable=True)
    media_duration_seconds = Column(Float, nullable=True)
    shopify_product_reference = Column(String(255), nullable=True)
    shopify_variant_reference = Column(String(255), nullable=True)
    product_handle = Column(String(255), nullable=True)
    product_title = Column(String(500), nullable=True)
    variant_title = Column(String(500), nullable=True)
    product_sku = Column(String(255), nullable=True)
    product_price = Column(Numeric(14, 2), nullable=True)
    product_currency = Column(String(8), nullable=True)
    product_image_url = Column(Text, nullable=True)
    product_url = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class WhatsAppConversation(Base):
    """Operational inbox state, keyed by a normalized WhatsApp phone number."""

    __tablename__ = "whatsapp_conversations"

    id = Column(Integer, primary_key=True)
    phone_number = Column(String(50), unique=True, nullable=False, index=True)
    customer_name = Column(String(255))
    mode = Column(String(10), nullable=False, default="AI", index=True)
    unread_count = Column(Integer, nullable=False, default=0)
    last_message_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    last_customer_message_at = Column(DateTime)
    taken_over_at = Column(DateTime)
    taken_over_by = Column(String(100))
    returned_to_ai_at = Column(DateTime)
    # Authoritative, restart-safe discovery state.  Message history remains useful
    # for AI context, but must never be used as the bicycle state machine.
    active_product_category = Column(String(50))
    workflow_state = Column(String(50), index=True)
    bicycle_gender = Column(String(10))
    bicycle_age = Column(Integer)
    bicycle_size = Column(Integer)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class WhatsAppManualSend(Base):
    """Server-side idempotency records for manual inbox sends."""

    __tablename__ = "whatsapp_manual_sends"

    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String(100), unique=True, nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey("whatsapp_conversations.id"), nullable=False)
    message_text = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="PROCESSING")
    whatsapp_message_id = Column(String(255))
    sent_by = Column(String(100))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WhatsAppOutboundProductMessage(Base):
    """A Meta message reference to a Shopify product, not a catalogue snapshot."""

    __tablename__ = "whatsapp_outbound_product_messages"

    id = Column(Integer, primary_key=True)
    whatsapp_message_id = Column(String(255), unique=True, nullable=False, index=True)
    customer_phone = Column(String(50), nullable=False, index=True)
    shopify_product_handle = Column(String(255), nullable=False, index=True)
    product_title = Column(String(500), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class WhatsAppOrderIntent(Base):
    """A resumable sales intent. It never represents a Shopify order or payment."""

    __tablename__ = "whatsapp_order_intents"

    id = Column(Integer, primary_key=True)
    customer_whatsapp_phone = Column(String(50), nullable=False, index=True)
    contact_person_name = Column(String(255))
    delivery_address = Column(Text)
    primary_phone = Column(String(20))
    alternative_phone = Column(String(20))
    shopify_product_reference = Column(String(255))
    shopify_product_handle = Column(String(255), nullable=False)
    shopify_variant_reference = Column(String(255))
    product_title = Column(String(500), nullable=False)
    variant_title = Column(String(500))
    product_url = Column(Text)
    product_image_url = Column(Text)
    product_price = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(8), nullable=False, default="LKR")
    product_category = Column(String(100), nullable=False)
    bicycle_gender = Column(String(10))
    bicycle_age = Column(Integer)
    bicycle_size = Column(Integer)
    initial_service_offered = Column(Boolean, nullable=False, default=False)
    initial_service_requested = Column(Boolean)
    initial_service_charge = Column(Numeric(14, 2), nullable=False, default=0)
    delivery_charge = Column(Numeric(14, 2), nullable=False, default=0)
    final_total = Column(Numeric(14, 2), nullable=False)
    status = Column(String(50), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    details_completed_at = Column(DateTime)
    payment_handover_at = Column(DateTime)


# ---------------------- AI Bot Management ----------------------
class BotConfiguration(Base):
    """The mutable draft and live pointer. Published history lives separately."""

    __tablename__ = "bot_configurations"
    id = Column(Integer, primary_key=True)
    published_version = Column(Integer, nullable=False, default=1)
    draft_revision = Column(Integer, nullable=False, default=0)
    published_snapshot = Column(JSON, nullable=False)
    draft_snapshot = Column(JSON, nullable=False)
    published_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class BotConfigurationVersion(Base):
    __tablename__ = "bot_configuration_versions"
    id = Column(Integer, primary_key=True)
    version = Column(Integer, unique=True, nullable=False, index=True)
    snapshot = Column(JSON, nullable=False)
    change_summary = Column(Text, nullable=False)
    published_by = Column(String(100))
    published_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BotKnowledgeEntry(Base):
    __tablename__ = "bot_knowledge_entries"
    id = Column(Integer, primary_key=True)
    kind = Column(String(20), nullable=False, default="KNOWLEDGE")
    scope = Column(String(20), nullable=False, default="GLOBAL")
    scope_reference = Column(String(255))
    title = Column(String(255), nullable=False)
    topic = Column(String(100))
    content = Column(Text, nullable=False)
    tags = Column(String(500))
    enabled = Column(Boolean, nullable=False, default=True)
    archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class BotAuditEvent(Base):
    __tablename__ = "bot_audit_events"
    id = Column(Integer, primary_key=True)
    action = Column(String(80), nullable=False)
    summary = Column(Text, nullable=False)
    actor = Column(String(100))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class ShopifyCollectionReference(Base):
    """Lightweight, read-only directory metadata last observed in Shopify."""

    __tablename__ = "shopify_collection_references"
    id = Column(Integer, primary_key=True)
    shopify_id = Column(String(255), unique=True, nullable=False, index=True)
    handle = Column(String(255), unique=True, nullable=False, index=True)
    title = Column(String(500), nullable=False, index=True)
    product_count = Column(Integer)
    active = Column(Boolean, nullable=False, default=True, index=True)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    refreshed_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class BotGuidedFlowState(Base):
    """Version-pinned structured state for a live generic guided flow."""

    __tablename__ = "bot_guided_flow_states"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("whatsapp_conversations.id"),
                             unique=True, nullable=False, index=True)
    configuration_version = Column(Integer, nullable=False, index=True)
    collection_reference = Column(String(255), nullable=False, index=True)
    current_question_key = Column(String(100))
    normalized_answers = Column(JSON, nullable=False, default=dict)
    status = Column(String(20), nullable=False, default="ACTIVE", index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)
