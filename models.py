from sqlalchemy import Boolean, Column, Integer, String, Date, DateTime, ForeignKey, Numeric, Text, Float, Enum, JSON, UniqueConstraint, CheckConstraint, event, func
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
    shopify_draft_order_name = Column(String(100), index=True)
    shopify_order_id = Column(String(255), unique=True)
    shopify_order_name = Column(String(100), index=True)
    shopify_order_created_at = Column(DateTime)
    shopify_order_linked_at = Column(DateTime)
    shopify_source_draft_order_id = Column(String(255), index=True)
    shopify_lifecycle_status = Column(String(40), nullable=False, default="NOT_LINKED", index=True)
    shopify_lifecycle_synced_at = Column(DateTime)
    shopify_lifecycle_error = Column(String(500))
    shopify_financial_status = Column(String(40), index=True)
    shopify_payment_status_refreshed_at = Column(DateTime)
    shopify_payment_reconciliation_status = Column(String(40), nullable=False,
                                                    default="NOT_CHECKED", index=True)
    shopify_payment_reconciliation_warning = Column(String(500))
    shopify_total_received = Column(Numeric(14, 2))
    shopify_total_outstanding = Column(Numeric(14, 2))
    shopify_total_refunded = Column(Numeric(14, 2))
    shopify_payment_currency = Column(String(8))
    shopify_sync_updated_at = Column(DateTime)
    shopify_sync_error = Column(String(500))
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
    idempotency_key = Column(String(100), unique=True, index=True)
    request_payload_hash = Column(String(64))
    deduplication_key = Column(String(255), unique=True, index=True)
    payment_method = Column(String(40), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(8), nullable=False, default="LKR")
    payment_date = Column(Date, nullable=False)
    verification_status = Column(String(40), nullable=False, default="PENDING", index=True)
    source = Column(String(40), nullable=False, default="STAFF_RECORDED")
    claimed_by = Column(String(100))
    claimed_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    verified_by = Column(String(100))
    verified_at = Column(DateTime)
    rejected_by = Column(String(100))
    rejected_at = Column(DateTime)
    rejection_reason = Column(Text)
    accounting_entry_id = Column(Integer, ForeignKey("journal_entries.id"))
    notes = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesPaymentAuditEvent(Base):
    __tablename__ = "sales_payment_audit_events"
    id = Column(Integer, primary_key=True)
    sales_payment_id = Column(Integer, ForeignKey("sales_payments.id", ondelete="RESTRICT"),
                              nullable=False, index=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id", ondelete="RESTRICT"),
                            nullable=False, index=True)
    action = Column(String(40), nullable=False, index=True)
    previous_status = Column(String(40))
    new_status = Column(String(40), nullable=False)
    actor = Column(String(100), nullable=False)
    reason = Column(Text)
    idempotency_key = Column(String(100), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


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
    actor = Column(String(100))
    claim_token = Column(String(64), index=True)
    claim_expires_at = Column(DateTime, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    processed_at = Column(DateTime)


class ShopifyPaymentSyncOperation(Base):
    __tablename__ = "shopify_payment_sync_operations"
    id = Column(Integer, primary_key=True)
    operation_id = Column(String(36), unique=True, nullable=False, index=True)
    idempotency_key = Column(String(100), unique=True, nullable=False)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    shopify_order_id = Column(String(255), nullable=False, index=True)
    # A nullable unique key is the portable equivalent of an active-operation partial index.
    # It remains populated for successful and unresolved operations and is cleared only when
    # read-only evidence proves a mutation was never issued.
    active_shopify_order_id = Column(String(255), unique=True, index=True)
    mutation_name = Column(String(80), nullable=False, default="orderMarkAsPaid")
    intended_amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(8), nullable=False)
    ledger_payment_fingerprint = Column(String(64), nullable=False)
    preflight_financial_status = Column(String(40), nullable=False)
    preflight_shopify_outstanding = Column(Numeric(14, 2), nullable=False)
    preflight_transaction_fingerprint = Column(String(64), nullable=False)
    status = Column(String(40), nullable=False, index=True)
    claim_token = Column(String(64), index=True)
    claim_expires_at = Column(DateTime, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    shopify_transaction_id = Column(String(255), index=True)
    error_summary = Column(String(500))
    approved_by = Column(String(100))
    approved_at = Column(DateTime)
    preflight_expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime)


class ShopifyPaymentSyncAuditEvent(Base):
    __tablename__ = "shopify_payment_sync_audit_events"
    id = Column(Integer, primary_key=True)
    operation_id = Column(Integer, ForeignKey("shopify_payment_sync_operations.id", ondelete="RESTRICT"), nullable=False, index=True)
    action = Column(String(50), nullable=False, index=True)
    actor = Column(String(100), nullable=False)
    detail = Column(String(500))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class SalesFulfillment(Base):
    __tablename__ = "sales_fulfillments"
    id = Column(Integer, primary_key=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    fulfillment_reference = Column(String(50), unique=True, nullable=False, index=True)
    status = Column(String(40), nullable=False, default="UNFULFILLED", index=True)
    created_by = Column(String(100), nullable=False)
    assigned_staff = Column(String(100), index=True)
    packing_notes = Column(Text)
    packing_started_at = Column(DateTime)
    ready_at = Column(DateTime)
    dispatched_at = Column(DateTime)
    delivered_at = Column(DateTime)
    version = Column(Integer, nullable=False, default=1)
    shopify_sync_status = Column(String(40), nullable=False, default="NOT_SYNCED", index=True)
    shopify_fulfillment_id = Column(String(255), unique=True)
    shopify_fulfillment_status = Column(String(40))
    shopify_synced_at = Column(DateTime)
    shopify_remote_updated_at = Column(DateTime)
    shopify_sync_error = Column(String(500))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesFulfillmentItem(Base):
    __tablename__ = "sales_fulfillment_items"
    __table_args__ = (UniqueConstraint("fulfillment_id", "sales_order_line_id", name="uq_fulfillment_order_line"),)
    id = Column(Integer, primary_key=True)
    fulfillment_id = Column(Integer, ForeignKey("sales_fulfillments.id", ondelete="RESTRICT"), nullable=False, index=True)
    sales_order_line_id = Column(Integer, ForeignKey("sales_order_lines.id", ondelete="RESTRICT"), nullable=False, index=True)
    quantity = Column(Numeric(14, 3), nullable=False)
    quantity_fulfilled = Column(Numeric(14, 3), nullable=False, default=0)
    quantity_returned = Column(Numeric(14, 3), nullable=False, default=0)
    shopify_fulfillment_order_id = Column(String(255), index=True)
    shopify_fulfillment_order_line_item_id = Column(String(255), index=True)


class SalesDeliveryDetail(Base):
    __tablename__ = "sales_delivery_details"
    id = Column(Integer, primary_key=True)
    fulfillment_id = Column(Integer, ForeignKey("sales_fulfillments.id", ondelete="RESTRICT"), unique=True, nullable=False)
    delivery_method = Column(String(40), nullable=False)
    courier_name = Column(String(120))
    courier_reference = Column(String(255))
    tracking_number = Column(String(255))
    tracking_url = Column(Text)
    contact_snapshot = Column(String(255))
    address_snapshot = Column(Text)
    expected_delivery_date = Column(Date)
    dispatch_date = Column(Date)
    delivery_date = Column(Date)
    delivery_remarks = Column(Text)
    proof_of_delivery_reference = Column(String(500))
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SalesFulfillmentEvent(Base):
    __tablename__ = "sales_fulfillment_events"
    id = Column(Integer, primary_key=True)
    fulfillment_id = Column(Integer, ForeignKey("sales_fulfillments.id", ondelete="RESTRICT"), nullable=False, index=True)
    previous_status = Column(String(40))
    new_status = Column(String(40), nullable=False, index=True)
    actor = Column(String(100), nullable=False)
    reason = Column(Text)
    metadata_json = Column(Text)
    idempotency_key = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class ShopifyFulfillmentOperation(Base):
    __tablename__ = "shopify_fulfillment_operations"
    id = Column(Integer, primary_key=True)
    operation_id = Column(String(36), unique=True, nullable=False, index=True)
    idempotency_key = Column(String(100), unique=True, nullable=False)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False, index=True)
    fulfillment_id = Column(Integer, ForeignKey("sales_fulfillments.id", ondelete="RESTRICT"), nullable=False, index=True)
    shopify_order_id = Column(String(255), nullable=False, index=True)
    active_fulfillment_id = Column(Integer, unique=True, index=True)
    request_fingerprint = Column(String(64), nullable=False)
    preflight_json = Column(Text, nullable=False)
    status = Column(String(40), nullable=False, index=True)
    claim_token = Column(String(64), index=True)
    claim_expires_at = Column(DateTime, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    approved_by = Column(String(100))
    approved_at = Column(DateTime)
    preflight_expires_at = Column(DateTime, nullable=False)
    shopify_fulfillment_id = Column(String(255), index=True)
    error_summary = Column(String(500))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime)


class ShopifyFulfillmentWebhookEvent(Base):
    __tablename__ = "shopify_fulfillment_webhook_events"
    id = Column(Integer, primary_key=True)
    webhook_id = Column(String(255), unique=True, nullable=False, index=True)
    topic = Column(String(100), nullable=False)
    shop_domain = Column(String(255), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    shopify_fulfillment_id = Column(String(255), index=True)
    fulfillment_id = Column(Integer, ForeignKey("sales_fulfillments.id", ondelete="RESTRICT"), index=True)
    status = Column(String(40), nullable=False, index=True)
    error_summary = Column(String(500))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    processed_at = Column(DateTime)


class ShopifyWebhookEvent(Base):
    __tablename__ = "shopify_webhook_events"
    id = Column(Integer, primary_key=True)
    webhook_id = Column(String(255), unique=True, nullable=False, index=True)
    topic = Column(String(100), nullable=False, index=True)
    shop_domain = Column(String(255), nullable=False, index=True)
    payload_hash = Column(String(64), nullable=False)
    shopify_order_id = Column(String(255), index=True)
    sales_order_id = Column(Integer, ForeignKey("sales_orders.id", ondelete="RESTRICT"), index=True)
    status = Column(String(40), nullable=False, index=True)
    attempts = Column(Integer, nullable=False, default=1)
    claim_token = Column(String(64), index=True)
    claim_expires_at = Column(DateTime, index=True)
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

    whatsapp_conversation_id = Column(Integer, ForeignKey("whatsapp_conversations.id", ondelete="RESTRICT"), nullable=True, unique=True, index=True)

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
    # NULL means this receipt was never admitted for automatic replies. Never
    # fill it retrospectively: OFF-period messages must not replay on re-enable.
    ai_reply_generation = Column(Integer, nullable=True)


class WhatsAppConversationMessage(Base):
    """A small, provider-token-free history used for customer-service context."""

    __tablename__ = "whatsapp_conversation_messages"

    id = Column(Integer, primary_key=True)
    phone_number = Column(String(50), nullable=False, index=True)
    customer_name = Column(String(255), nullable=True)
    direction = Column(String(20), nullable=False)
    message_text = Column(Text, nullable=False)
    whatsapp_message_id = Column(String(255), nullable=True, index=True)
    reply_to_whatsapp_message_id = Column(String(255), nullable=True)
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
    referral = relationship("WhatsAppMessageReferral", back_populates="message", uselist=False,
                            passive_deletes="all")


class WhatsAppMessageReferral(Base):
    """Private, allowlisted advertisement attribution; never catalogue authority."""

    __tablename__ = "whatsapp_message_referrals"
    __table_args__ = (UniqueConstraint("message_id", name="uq_whatsapp_message_referrals_message_id"),)

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("whatsapp_conversation_messages.id", ondelete="RESTRICT"), nullable=False)
    source_id = Column(String(255), nullable=True)
    source_type = Column(String(32), nullable=True)
    source_url = Column(String(2048), nullable=True)
    headline = Column(String(500), nullable=True)
    body = Column(String(4000), nullable=True)
    media_type = Column(String(32), nullable=True)
    image_url = Column(String(2048), nullable=True)
    video_url = Column(String(2048), nullable=True)
    thumbnail_url = Column(String(2048), nullable=True)
    ctwa_clid = Column(String(1024), nullable=True)
    captured_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    message = relationship("WhatsAppConversationMessage", back_populates="referral")


class WhatsAppMessageAttachment(Base):
    """Provider attachment metadata only; never stores bytes, tokens or media URLs.

    Phase 1 deliberately leaves existing voice fields and message handling intact.
    One message can have multiple attachments, identified by provider media ID.
    """

    __tablename__ = "whatsapp_message_attachments"
    __table_args__ = (
        UniqueConstraint("message_id", "media_id", name="uq_whatsapp_attachment_message_media"),
    )

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("whatsapp_conversation_messages.id", ondelete="RESTRICT"),
                        nullable=False, index=True)
    media_id = Column(String(255), nullable=False)
    media_type = Column(String(30), nullable=False)
    mime_type = Column(String(100), nullable=True)
    filename = Column(String(255), nullable=True)
    caption = Column(Text, nullable=True)
    sha256 = Column(String(64), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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
    audio_trace = Column(JSON)
    idempotency_key = Column(String(100), unique=True, nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey("whatsapp_conversations.id"), nullable=False)
    quick_message_id = Column(Integer, ForeignKey("whatsapp_quick_messages.id", ondelete="RESTRICT"))
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
class GlobalAIReplyControl(Base):
    """Immediate operational setting, independent of draft/live and learning."""
    __tablename__ = "global_ai_reply_control"
    __table_args__ = (CheckConstraint("id = 1", name="ck_global_ai_singleton"),)
    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    generation = Column(Integer, nullable=False, default=0)
    updated_by = Column(String(100))
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


@event.listens_for(GlobalAIReplyControl.__table__, "after_create")
def _seed_global_ai_control(target, connection, **kw):
    connection.execute(target.insert().values(id=1, enabled=True, generation=0))


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


class WhatsAppMediaSend(Base):
    """Durable staff-media send claim; metadata only, never uploaded file bytes."""
    __tablename__ = "whatsapp_media_sends"
    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String(100), nullable=False, unique=True)
    conversation_id = Column(Integer, ForeignKey("whatsapp_conversations.id", ondelete="RESTRICT"), nullable=False)
    staff_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False)
    media_id = Column(String(255))
    message_id = Column(Integer, ForeignKey("whatsapp_conversation_messages.id", ondelete="RESTRICT"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SavedVoice(Base):
    __tablename__ = "saved_voices"
    id = Column(Integer, primary_key=True)
    title = Column(String(100), nullable=False)
    category = Column(String(80), nullable=False)
    transcript = Column(Text, nullable=False)
    language = Column(String(16), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    revision = Column(Integer, nullable=False, default=1)
    storage_name = Column(String(40), nullable=False, unique=True)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    duration_seconds = Column(Float, nullable=False)
    original_storage_name = Column(String(40))
    normalization_version = Column(Integer, nullable=False, default=0)
    processing_status = Column(String(24), nullable=False, default="PENDING")
    normalization_error = Column(String(200))
    normalization_token = Column(String(32))
    normalization_started_at = Column(DateTime)
    audio_diagnostics = Column(JSON)
    created_by = Column(String(100), nullable=False)
    updated_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class SavedVoiceSend(Base):
    __tablename__ = "saved_voice_sends"
    id = Column(Integer, primary_key=True)
    audio_trace = Column(JSON)
    idempotency_key = Column(String(100), nullable=False, unique=True)
    conversation_id = Column(Integer, ForeignKey("whatsapp_conversations.id", ondelete="RESTRICT"), nullable=False)
    staff_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    voice_id = Column(Integer, ForeignKey("saved_voices.id", ondelete="RESTRICT"), nullable=False)
    voice_revision = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False)
    delivery_status = Column(String(24))
    delivery_error_code = Column(Integer)
    media_id = Column(String(255))
    whatsapp_message_id = Column(String(255), index=True)
    message_id = Column(Integer, ForeignKey("whatsapp_conversation_messages.id", ondelete="RESTRICT"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class WhatsAppQuickMessage(Base):
    """Editable staff reply snippets, distinct from Meta-approved templates."""
    __tablename__ = 'whatsapp_quick_messages'
    id = Column(Integer, primary_key=True)
    title = Column(String(100), nullable=False)
    shortcut = Column(String(40), unique=True, nullable=False)
    category = Column(String(80), nullable=False)
    language = Column(String(2), nullable=False)
    body = Column(Text, nullable=False)
    active = Column(Boolean, nullable=False, default=True, server_default='1')
    display_order = Column(Integer, nullable=False, default=0, server_default='0')
    usage_count = Column(Integer, nullable=False, default=0, server_default='0')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String(100))
    updated_by = Column(String(100))

# Register additive learning tables with the same Base/Alembic metadata.
from learning_models import (LearningSettings, LearningLease, LearningSource, LearningJob,
    LearningAnalysis, LearningCandidate, LearningEvidence, LearningEvaluation,
    LearningKnowledgeVersion, LearningAudit, LearningAction, LearningFeedback)
from learning_models import LearningReservation
from learning_models import LearningRegressionCase
