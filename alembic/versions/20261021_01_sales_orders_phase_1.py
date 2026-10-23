"""Add Phase 1 commercial sales-order foundation.

Revision ID: 20261021_01
This migration is additive and baselines the existing create_all-managed production schema.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_01"
down_revision = None
branch_labels = None
depends_on = None


def _has_table(name):
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("users") and "role" not in {c["name"] for c in inspector.get_columns("users")}:
        op.add_column("users", sa.Column("role", sa.String(30), nullable=False,
                                        server_default="STAFF"))
        op.create_index("ix_users_role", "users", ["role"])

    if not _has_table("sales_orders"):
        op.create_table("sales_orders",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("internal_order_number", sa.String(40), nullable=False),
            sa.Column("idempotency_key", sa.String(100), nullable=False),
            sa.Column("crm_conversation_id", sa.Integer(), sa.ForeignKey("whatsapp_conversations.id")),
            sa.Column("crm_lead_id", sa.Integer(), sa.ForeignKey("leads.id")),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id")),
            sa.Column("customer_name", sa.String(255), nullable=False),
            sa.Column("whatsapp_phone", sa.String(50), nullable=False),
            sa.Column("alternative_phone", sa.String(50)),
            sa.Column("delivery_contact_person", sa.String(255)), sa.Column("delivery_address", sa.Text()),
            sa.Column("city", sa.String(120)), sa.Column("district", sa.String(120)),
            sa.Column("postal_code", sa.String(30)), sa.Column("customer_notes", sa.Text()),
            sa.Column("order_status", sa.String(40), nullable=False, server_default="DRAFT"),
            sa.Column("payment_status", sa.String(40), nullable=False, server_default="UNPAID"),
            sa.Column("fulfillment_status", sa.String(40), nullable=False, server_default="UNFULFILLED"),
            sa.Column("shopify_sync_status", sa.String(40), nullable=False, server_default="NOT_SYNCED"),
            sa.Column("shopify_draft_order_id", sa.String(255)),
            sa.Column("shopify_order_id", sa.String(255)), sa.Column("shopify_order_name", sa.String(100)),
            sa.Column("subtotal", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("discount_total", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("shipping_total", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("tax_total", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("grand_total", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("amount_paid", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(8), nullable=False, server_default="LKR"),
            sa.Column("notes", sa.Text()), sa.Column("created_by", sa.String(100), nullable=False),
            sa.Column("confirmed_by", sa.String(100)),
            sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("idempotency_key"),
            sa.UniqueConstraint("shopify_draft_order_id"), sa.UniqueConstraint("shopify_order_id"))
        for column in ("internal_order_number", "crm_conversation_id", "crm_lead_id", "customer_id",
                       "whatsapp_phone", "order_status", "payment_status", "fulfillment_status",
                       "shopify_sync_status", "shopify_order_name", "created_by", "created_at"):
            op.create_index(f"ix_sales_orders_{column}", "sales_orders", [column],
                            unique=column == "internal_order_number")

    if not _has_table("sales_order_lines"):
        op.create_table("sales_order_lines",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False),
            sa.Column("inventory_item_id", sa.Integer(), sa.ForeignKey("items.id")),
            sa.Column("shopify_product_id", sa.String(255), nullable=False),
            sa.Column("shopify_variant_id", sa.String(255), nullable=False), sa.Column("sku", sa.String(255)),
            sa.Column("product_name_snapshot", sa.String(500), nullable=False),
            sa.Column("variant_name_snapshot", sa.String(500)),
            sa.Column("quantity", sa.Numeric(14, 3), nullable=False),
            sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
            sa.Column("discount_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("tax_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("line_total", sa.Numeric(14, 2), nullable=False),
            sa.Column("unit_cost_snapshot", sa.Numeric(14, 2)))
        for column in ("sales_order_id", "inventory_item_id", "shopify_product_id", "shopify_variant_id", "sku"):
            op.create_index(f"ix_sales_order_lines_{column}", "sales_order_lines", [column])

    if not _has_table("sales_payments"):
        op.create_table("sales_payments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("payment_reference", sa.String(255)), sa.Column("payment_method", sa.String(40), nullable=False),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False), sa.Column("payment_date", sa.Date(), nullable=False),
            sa.Column("verification_status", sa.String(40), nullable=False, server_default="PENDING"),
            sa.Column("verified_by", sa.String(100)), sa.Column("verified_at", sa.DateTime()),
            sa.Column("accounting_entry_id", sa.Integer(), sa.ForeignKey("journal_entries.id")),
            sa.Column("notes", sa.Text()), sa.Column("created_at", sa.DateTime(), nullable=False))
        for column in ("sales_order_id", "payment_reference", "verification_status"):
            op.create_index(f"ix_sales_payments_{column}", "sales_payments", [column])

    if not _has_table("sales_order_status_history"):
        op.create_table("sales_order_status_history",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id", ondelete="CASCADE"), nullable=False),
            sa.Column("previous_status", sa.String(40), nullable=False),
            sa.Column("new_status", sa.String(40), nullable=False),
            sa.Column("changed_by", sa.String(100), nullable=False), sa.Column("reason", sa.Text()),
            sa.Column("created_at", sa.DateTime(), nullable=False))
        for column in ("sales_order_id", "new_status", "created_at"):
            op.create_index(f"ix_sales_order_status_history_{column}", "sales_order_status_history", [column])

    if not _has_table("shopify_sync_events"):
        op.create_table("shopify_sync_events",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("event_id", sa.String(255)),
            sa.Column("event_type", sa.String(80), nullable=False),
            sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id", ondelete="RESTRICT")),
            sa.Column("external_reference", sa.String(255)), sa.Column("direction", sa.String(20), nullable=False),
            sa.Column("idempotency_key", sa.String(100), nullable=False),
            sa.Column("payload_hash", sa.String(64), nullable=False), sa.Column("status", sa.String(40), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_summary", sa.String(500)), sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("processed_at", sa.DateTime()), sa.UniqueConstraint("idempotency_key"))
        for column in ("event_id", "event_type", "sales_order_id", "external_reference", "status", "created_at"):
            op.create_index(f"ix_shopify_sync_events_{column}", "shopify_sync_events", [column],
                            unique=column == "event_id")

    if not _has_table("accounting_posting_links"):
        op.create_table("accounting_posting_links",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source_document_type", sa.String(50), nullable=False),
            sa.Column("source_document_id", sa.Integer(), nullable=False),
            sa.Column("posting_event_type", sa.String(50), nullable=False),
            sa.Column("journal_entry_id", sa.Integer(), sa.ForeignKey("journal_entries.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("reversal_reference", sa.Integer(), sa.ForeignKey("accounting_posting_links.id")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("journal_entry_id"),
            sa.UniqueConstraint("source_document_type", "source_document_id", "posting_event_type",
                                name="uq_accounting_posting_source_event"))

    if inspector.has_table("users"):
        bind.execute(sa.text("UPDATE users SET role = 'ADMIN' WHERE username = 'admin'"))


def downgrade():
    raise RuntimeError("Sales-order migration downgrade is intentionally non-destructive; disable feature flags instead")
