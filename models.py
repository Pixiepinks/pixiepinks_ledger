from sqlalchemy import Boolean, Column, Integer, String, Date, DateTime, ForeignKey, Numeric, Text, Float, Enum, JSON, UniqueConstraint, CheckConstraint, event, func, false
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
    section_access = Column(JSON, nullable=True)  # NULL preserves pre-existing role behavior.
    is_active = Column(Boolean, nullable=False, default=True, server_default='1')
    session_version = Column(Integer, nullable=False, default=0, server_default='0')


class StaffAccessAudit(Base):
    __tablename__ = 'staff_access_audits'
    id = Column(Integer, primary_key=True)
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    target_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    action = Column(String(40), nullable=False)
    changes = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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
    service_total = Column(Numeric(14, 2), nullable=False, default=0, server_default="0")
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
    analytics_actor_id = Column(Integer, nullable=True, index=True)
    analytics_actor_kind = Column(String(16), nullable=True)
    analytics_category = Column(String(24), nullable=True)
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
    assigned_to_user_id = Column(Integer,ForeignKey('users.id',name='fk_whatsapp_assigned_user'),nullable=True,index=True)
    assigned_by_user_id = Column(Integer,ForeignKey('users.id',name='fk_whatsapp_assigning_user'),nullable=True)
    assigned_at = Column(DateTime,nullable=True)
    assignment_version = Column(Integer,nullable=False,default=0,server_default='0')
    # Authoritative, restart-safe discovery state.  Message history remains useful
    # for AI context, but must never be used as the bicycle state machine.
    ad_flow_opted_out=Column(Boolean,nullable=False,default=False,server_default=false())
    active_product_category = Column(String(50))
    workflow_state = Column(String(50), index=True)
    bicycle_gender = Column(String(10))
    bicycle_age = Column(Integer)
    bicycle_size = Column(Integer)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class ConversationAssignmentAudit(Base):
    __tablename__='conversation_assignment_audits'
    id=Column(Integer,primary_key=True)
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id'),nullable=False,index=True)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    previous_owner_id=Column(Integer,ForeignKey('users.id'),nullable=True)
    new_owner_id=Column(Integer,ForeignKey('users.id'),nullable=True)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    source=Column(String(24),nullable=False,default='MANUAL',server_default='MANUAL')
    ad_id=Column(String(64),nullable=True)
    rule_id=Column(Integer,nullable=True,index=True)  # Immutable audit reference; rules are disabled, never deleted.
    rule_version=Column(Integer,nullable=True)


class AdAssignmentRule(Base):
    __tablename__='ad_assignment_rules'
    id=Column(Integer,primary_key=True)
    ad_id=Column(String(64),nullable=False,unique=True,index=True)
    label=Column(String(120),nullable=False,default='')
    staff_user_id=Column(Integer,ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    enabled=Column(Boolean,nullable=False,default=True)
    version=Column(Integer,nullable=False,default=1)
    created_by_user_id=Column(Integer,ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    updated_by_user_id=Column(Integer,ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class AdAssignmentRuleAudit(Base):
    __tablename__='ad_assignment_rule_audits'
    __table_args__=(UniqueConstraint('rule_id','version',name='uq_ad_assignment_rule_audit_version'),)
    id=Column(Integer,primary_key=True)
    rule_id=Column(Integer,ForeignKey('ad_assignment_rules.id',ondelete='RESTRICT'),nullable=False,index=True)
    actor_id=Column(Integer,ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    version=Column(Integer,nullable=False)
    changes=Column(JSON,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class WhatsAppManualSend(Base):
    """Server-side idempotency records for manual inbox sends."""

    __tablename__ = "whatsapp_manual_sends"

    id = Column(Integer, primary_key=True)
    audio_trace = Column(JSON)
    idempotency_key = Column(String(100), unique=True, nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey("whatsapp_conversations.id"), nullable=False)
    reply_to_whatsapp_message_id = Column(String(255), nullable=True)
    quick_message_id = Column(Integer, ForeignKey("whatsapp_quick_messages.id", ondelete="RESTRICT"))
    analytics_actor_id = Column(Integer, nullable=True, index=True)
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


class VoicePlaybackTest(Base):
    """Owner-only immutable A/B review and durable single execution claim."""
    __tablename__ = "voice_playback_tests"
    id = Column(String(32), primary_key=True)
    staff_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    voice_id = Column(Integer, ForeignKey("saved_voices.id", ondelete="RESTRICT"), nullable=False)
    voice_revision = Column(Integer, nullable=False)
    storage_name = Column(String(40), nullable=False)
    recipient_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="REVIEW")
    evidence = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    confirmed_at = Column(DateTime)
    results_at = Column(DateTime)
    results_by = Column(Integer)
    playback = Column(JSON)


class VoicePlaybackVariant(Base):
    __tablename__ = "voice_playback_variants"
    id = Column(Integer, primary_key=True)
    run_id = Column(String(32), ForeignKey("voice_playback_tests.id", ondelete="RESTRICT"), nullable=False)
    variant = Column(String(1), nullable=False)
    status = Column(String(24), nullable=False, default="NOT_STARTED")
    audio_trace = Column(JSON)
    media_id = Column(String(255))
    whatsapp_message_id = Column(String(255), index=True)
    delivery_status = Column(String(24))
    delivery_error_code = Column(Integer)
    __table_args__ = (UniqueConstraint("run_id", "variant", name="uq_voice_playback_variant"),)


class ConversationShare(Base):
    __tablename__='conversation_shares'
    __table_args__=(UniqueConstraint('conversation_id','user_id',name='uq_conversation_share_user'),)
    id=Column(Integer,primary_key=True)
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id'),nullable=False,index=True)
    user_id=Column(Integer,ForeignKey('users.id'),nullable=False,index=True)
    can_reply=Column(Boolean,nullable=False,default=False)
    granted_by=Column(Integer,ForeignKey('users.id'),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class ConversationShareAudit(Base):
    __tablename__='conversation_share_audits'
    id=Column(Integer,primary_key=True)
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id'),nullable=False,index=True)
    actor_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    user_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    action=Column(String(20),nullable=False)
    can_reply=Column(Boolean,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class ConversationNote(Base):
    __tablename__='conversation_notes'
    __table_args__=(UniqueConstraint('conversation_id','author_id','client_key',name='uq_conversation_note_key'),)
    id=Column(Integer,primary_key=True)
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id'),nullable=False,index=True)
    author_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    client_key=Column(String(80),nullable=False)
    body=Column(Text,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class ConversationMention(Base):
    __tablename__='conversation_mentions'
    __table_args__=(UniqueConstraint('note_id','user_id',name='uq_conversation_mention_user'),)
    id=Column(Integer,primary_key=True)
    note_id=Column(Integer,ForeignKey('conversation_notes.id'),nullable=False,index=True)
    user_id=Column(Integer,ForeignKey('users.id'),nullable=False,index=True)
    read_at=Column(DateTime)


class MarketingConsent(Base):
    """One marketing state per canonical phone identity; never a new Customer."""
    __tablename__='marketing_consents'
    __table_args__=(CheckConstraint("status IN ('NOT_REQUESTED','PENDING','OPTED_IN','OPTED_OUT')",name='ck_marketing_consent_status'),
        CheckConstraint("status != 'OPTED_IN' OR (phone_e164 IS NOT NULL AND affirmative = true AND scope = 'PIXIEPINKS_WHATSAPP_PROMOTIONS' AND evidence_reference IS NOT NULL AND evidence_at IS NOT NULL AND wording IS NOT NULL AND wording_version IS NOT NULL AND consent_at IS NOT NULL AND last_verified_at IS NOT NULL)",name='ck_marketing_consent_evidence'))
    id=Column(Integer,primary_key=True)
    identity_key=Column(String(100),nullable=False,unique=True)
    phone_e164=Column(String(18),nullable=True,unique=True)
    customer_id=Column(Integer,ForeignKey('customers.id',ondelete='SET NULL'),index=True)
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id',ondelete='SET NULL'),index=True)
    lead_id=Column(Integer,ForeignKey('leads.id',ondelete='SET NULL'),index=True)
    display_name=Column(String(255),nullable=False)
    identity_conflict=Column(Boolean,nullable=False,default=False,server_default='0')
    status=Column(String(24),nullable=False,default='NOT_REQUESTED',server_default='NOT_REQUESTED',index=True)
    scope=Column(String(100),nullable=False,default='PIXIEPINKS_WHATSAPP_PROMOTIONS',server_default='PIXIEPINKS_WHATSAPP_PROMOTIONS')
    source=Column(String(40),index=True)
    source_description=Column(Text)
    consent_at=Column(DateTime)
    wording_version=Column(String(100))
    wording=Column(Text)
    evidence_type=Column(String(30))
    evidence_reference=Column(String(500))
    evidence_at=Column(DateTime)
    affirmative=Column(Boolean,nullable=False,default=False,server_default='0')
    verified_by=Column(Integer,ForeignKey('users.id',ondelete='SET NULL'))
    last_verified_at=Column(DateTime)
    opted_out_at=Column(DateTime)
    opt_out_source=Column(String(40))
    opt_out_reason=Column(String(500))
    interests=Column(JSON,nullable=False,default=list)
    needs_review=Column(Boolean,nullable=False,default=False,server_default='0')
    active_request_id=Column(Integer)
    version=Column(Integer,nullable=False,default=0,server_default='0')
    created_by=Column(Integer,ForeignKey('users.id',ondelete='SET NULL'))
    updated_by=Column(Integer,ForeignKey('users.id',ondelete='SET NULL'))
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)


class MarketingConsentRequest(Base):
    __tablename__='marketing_consent_requests'
    id=Column(Integer,primary_key=True)
    consent_id=Column(Integer,ForeignKey('marketing_consents.id'),nullable=False,index=True)
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id',ondelete='SET NULL'),index=True)
    phone_e164=Column(String(18),nullable=False)
    actor_id=Column(Integer,ForeignKey('users.id',ondelete='SET NULL'))
    idempotency_key=Column(String(100),nullable=False,unique=True)
    language=Column(String(8),nullable=False)
    wording_version=Column(String(100),nullable=False)
    message_text=Column(Text,nullable=False)
    status=Column(String(20),nullable=False,default='PREPARED')
    base_version=Column(Integer,nullable=False)
    whatsapp_message_id=Column(String(255),index=True)
    local_message_id=Column(Integer)
    delivery_status=Column(String(24))
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    sent_at=Column(DateTime)
    expires_at=Column(DateTime,nullable=False)


class MarketingConsentEvent(Base):
    __tablename__='marketing_consent_events'
    id=Column(Integer,primary_key=True)
    consent_id=Column(Integer,ForeignKey('marketing_consents.id'),nullable=False,index=True)
    dedup_key=Column(String(150),nullable=False,unique=True)
    action_fingerprint=Column(String(64))
    event_type=Column(String(40),nullable=False)
    previous_status=Column(String(24),nullable=False)
    new_status=Column(String(24),nullable=False)
    customer_id=Column(Integer)
    conversation_id=Column(Integer)
    phone_e164=Column(String(18))
    source=Column(String(40))
    scope=Column(String(100),nullable=False)
    wording_version=Column(String(100))
    wording=Column(Text)
    evidence_type=Column(String(30))
    evidence_reference=Column(String(500))
    evidence_text=Column(Text)
    evidence_at=Column(DateTime)
    source_description=Column(Text)
    actor_id=Column(Integer)
    actor_name=Column(String(100),nullable=False)
    reason=Column(String(500))
    recorded_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)
    occurred_at=Column(DateTime,nullable=False,default=datetime.utcnow)


@event.listens_for(MarketingConsentEvent,'before_update')
@event.listens_for(MarketingConsentEvent,'before_delete')
def _marketing_audit_immutable(*args):
    raise ValueError('Marketing consent events are append-only')


class CRMAnalyticsPhone(Base):
    __tablename__='crm_analytics_phones'
    raw_phone=Column(String(64),primary_key=True)
    canonical_phone=Column(String(18),index=True)
    country=Column(String(2),nullable=False,index=True)
    country_name=Column(String(100),nullable=False)


class CRMAnalyticsDelivery(Base):
    __tablename__='crm_analytics_deliveries'
    provider_id=Column(String(255),primary_key=True)
    status=Column(String(16),nullable=False)
    status_at=Column(DateTime,nullable=False)
    rank=Column(Integer,nullable=False)


class ConversationWorkState(Base):
    __tablename__='conversation_work_states'
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id',ondelete='RESTRICT'),primary_key=True)
    status=Column(String(24),nullable=False,default='NEEDS_REPLY',server_default='NEEDS_REPLY',index=True)
    version=Column(Integer,nullable=False,default=0,server_default='0')
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    updated_by=Column(Integer,nullable=True)  # Retained actor snapshot, including deleted accounts.
    reason=Column(String(1000))
    resolved_at=Column(DateTime)
    due_at=Column(DateTime,index=True)
    followup_user_id=Column(Integer,nullable=True,index=True)
    inbound_id=Column(Integer,nullable=True)
    inbound_at=Column(DateTime)
    force_reply=Column(Boolean,nullable=False,default=False,server_default=false())
    baseline_id=Column(Integer,nullable=False,default=0,server_default='0')
    review_cutoff_at=Column(DateTime)
    ack_inbound_id=Column(Integer,nullable=False,default=0,server_default='0')
    __table_args__=(CheckConstraint("status IN ('NEEDS_REPLY','FOLLOW_UP_LATER','RESOLVED')",name='ck_conversation_work_status'),)


class ConversationWorkEvent(Base):
    __tablename__='conversation_work_events'
    id=Column(Integer,primary_key=True)
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id',ondelete='RESTRICT'),nullable=False,index=True)
    dedup_key=Column(String(160),nullable=False,unique=True)
    fingerprint=Column(String(64),nullable=False)
    previous_status=Column(String(24),nullable=False)
    new_status=Column(String(24),nullable=False)
    actor_id=Column(Integer)
    actor_name=Column(String(100))
    trigger=Column(String(16),nullable=False)
    reason=Column(String(1000))
    inbound_id=Column(Integer)
    due_at=Column(DateTime)
    followup_user_id=Column(Integer)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)


@event.listens_for(ConversationWorkEvent,'before_update')
@event.listens_for(ConversationWorkEvent,'before_delete')
def _conversation_work_event_immutable(*args):
    raise ValueError('Conversation status history is append-only')


class CustomerQuestionConfig(Base):
    __tablename__='customer_question_config'
    id=Column(Integer,primary_key=True)
    enabled=Column(Boolean,nullable=False,default=False,server_default=false())
    version=Column(Integer,nullable=False,default=0,server_default='0')
    definition=Column(JSON,nullable=False)


class CustomerQuestionFlow(Base):
    __tablename__='customer_question_flows'
    id=Column(Integer,primary_key=True)
    definition=Column(JSON,nullable=False)
    archived=Column(Boolean,nullable=False,default=False,server_default=false())
    version=Column(Integer,nullable=False,default=0,server_default='0')
    created_by=Column(String(100),nullable=False)
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class CustomerQuestionRun(Base):
    __tablename__='customer_question_runs'
    flow_id=Column(Integer,nullable=True)  # Retained library identity; snapshot remains authoritative.
    id=Column(Integer,primary_key=True)
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id',ondelete='RESTRICT'),nullable=False,index=True)
    definition=Column(JSON,nullable=False)  # Immutable snapshot for this run.
    status=Column(String(20),nullable=False)
    step=Column(Integer,nullable=False,default=0)
    answers=Column(JSON,nullable=False,default=dict)
    token=Column(String(64),nullable=False,unique=True)
    provider_id=Column(String(255))
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    created_by=Column(String(100),nullable=False)
    version=Column(Integer,nullable=False,default=0)
    __table_args__=(CheckConstraint("status IN ('QUEUED','SENDING','WAITING','PAUSED','STOPPED','COMPLETE','UNCERTAIN','WINDOW_CLOSED')",name='ck_customer_question_status'),)


class CustomerQuestionEvent(Base):
    __tablename__='customer_question_events'
    id=Column(Integer,primary_key=True)
    run_id=Column(Integer,ForeignKey('customer_question_runs.id',ondelete='RESTRICT'),index=True)
    action=Column(String(40),nullable=False)
    actor=Column(String(100),nullable=False)
    detail=Column(JSON,nullable=False,default=dict)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)


@event.listens_for(CustomerQuestionEvent,'before_update')
@event.listens_for(CustomerQuestionEvent,'before_delete')
def _customer_question_event_immutable(*args):
    raise ValueError('Customer question history is append-only')


class CustomerQuestionProductSend(Base):
    __tablename__='customer_question_product_sends'
    audio_snapshot=Column(JSON,nullable=True)
    choice_label=Column(String(24),nullable=True)  # Exact numbered label retained at card send.
    id=Column(Integer,primary_key=True)
    run_id=Column(Integer,ForeignKey('customer_question_runs.id',ondelete='RESTRICT'),nullable=False,index=True)
    step=Column(Integer,nullable=False)
    position=Column(Integer,nullable=False)
    status=Column(String(16),nullable=False)
    provider_id=Column(String(255))
    reason=Column(String(200))
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    __table_args__=(UniqueConstraint('run_id','step','position',name='uq_question_product_position'),
        CheckConstraint("status IN ('QUEUED','SENDING','SENT','SKIPPED','FAILED','UNCERTAIN')",name='ck_question_product_status'))

# Additive ad-trigger metadata; saved question runs remain the execution authority.
from ad_flow_models import AdFlowControl, AdFlowRule, AdFlowExecution, AdFlowAudit
