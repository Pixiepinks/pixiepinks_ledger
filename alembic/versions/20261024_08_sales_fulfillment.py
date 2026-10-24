"""Add operational fulfillment and Shopify synchronization records.

Revision ID: 20261024_08
Revises: 20261024_07
"""
from alembic import op
import sqlalchemy as sa

revision = "20261024_08"
down_revision = "20261024_07"
branch_labels = None
depends_on = None


def upgrade():
    if "sales_fulfillments" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table("sales_fulfillments",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("fulfillment_reference", sa.String(50), nullable=False, unique=True), sa.Column("status", sa.String(40), nullable=False, server_default="UNFULFILLED"),
        sa.Column("created_by", sa.String(100), nullable=False), sa.Column("assigned_staff", sa.String(100)), sa.Column("packing_notes", sa.Text()),
        sa.Column("packing_started_at", sa.DateTime()), sa.Column("ready_at", sa.DateTime()), sa.Column("dispatched_at", sa.DateTime()), sa.Column("delivered_at", sa.DateTime()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"), sa.Column("shopify_sync_status", sa.String(40), nullable=False, server_default="NOT_SYNCED"),
        sa.Column("shopify_fulfillment_id", sa.String(255), unique=True), sa.Column("shopify_fulfillment_status", sa.String(40)),
        sa.Column("shopify_synced_at", sa.DateTime()), sa.Column("shopify_remote_updated_at", sa.DateTime()), sa.Column("shopify_sync_error", sa.String(500)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_table("sales_fulfillment_items",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("fulfillment_id", sa.Integer(), sa.ForeignKey("sales_fulfillments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("sales_order_line_id", sa.Integer(), sa.ForeignKey("sales_order_lines.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quantity", sa.Numeric(14,3), nullable=False), sa.Column("quantity_fulfilled", sa.Numeric(14,3), nullable=False, server_default="0"),
        sa.Column("quantity_returned", sa.Numeric(14,3), nullable=False, server_default="0"), sa.Column("shopify_fulfillment_order_id", sa.String(255)),
        sa.Column("shopify_fulfillment_order_line_item_id", sa.String(255)), sa.UniqueConstraint("fulfillment_id", "sales_order_line_id", name="uq_fulfillment_order_line"))
    op.create_table("sales_delivery_details",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("fulfillment_id", sa.Integer(), sa.ForeignKey("sales_fulfillments.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("delivery_method", sa.String(40), nullable=False), sa.Column("courier_name", sa.String(120)), sa.Column("courier_reference", sa.String(255)),
        sa.Column("tracking_number", sa.String(255)), sa.Column("tracking_url", sa.Text()), sa.Column("contact_snapshot", sa.String(255)), sa.Column("address_snapshot", sa.Text()),
        sa.Column("expected_delivery_date", sa.Date()), sa.Column("dispatch_date", sa.Date()), sa.Column("delivery_date", sa.Date()),
        sa.Column("delivery_remarks", sa.Text()), sa.Column("proof_of_delivery_reference", sa.String(500)), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_table("sales_fulfillment_events",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("fulfillment_id", sa.Integer(), sa.ForeignKey("sales_fulfillments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("previous_status", sa.String(40)), sa.Column("new_status", sa.String(40), nullable=False), sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text()), sa.Column("metadata_json", sa.Text()), sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_table("shopify_fulfillment_operations",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("operation_id", sa.String(36), nullable=False, unique=True), sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("fulfillment_id", sa.Integer(), sa.ForeignKey("sales_fulfillments.id", ondelete="RESTRICT"), nullable=False), sa.Column("shopify_order_id", sa.String(255), nullable=False),
        sa.Column("active_fulfillment_id", sa.Integer(), unique=True), sa.Column("request_fingerprint", sa.String(64), nullable=False), sa.Column("preflight_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("claim_token", sa.String(64)), sa.Column("claim_expires_at", sa.DateTime()), sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("approved_by", sa.String(100)), sa.Column("approved_at", sa.DateTime()), sa.Column("preflight_expires_at", sa.DateTime(), nullable=False),
        sa.Column("shopify_fulfillment_id", sa.String(255)), sa.Column("error_summary", sa.String(500)), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()), sa.Column("completed_at", sa.DateTime()))
    op.create_table("shopify_fulfillment_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("webhook_id", sa.String(255), nullable=False, unique=True),
        sa.Column("topic", sa.String(100), nullable=False), sa.Column("shop_domain", sa.String(255), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False), sa.Column("shopify_fulfillment_id", sa.String(255)),
        sa.Column("fulfillment_id", sa.Integer(), sa.ForeignKey("sales_fulfillments.id", ondelete="RESTRICT")),
        sa.Column("status", sa.String(40), nullable=False), sa.Column("error_summary", sa.String(500)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()), sa.Column("processed_at", sa.DateTime()))
    for table, columns in {"sales_fulfillments":("sales_order_id","status","shopify_sync_status","created_at"), "sales_fulfillment_items":("fulfillment_id","sales_order_line_id"),
        "sales_fulfillment_events":("fulfillment_id","new_status","created_at"), "shopify_fulfillment_operations":("sales_order_id","fulfillment_id","shopify_order_id","status","claim_token"),
        "shopify_fulfillment_webhook_events":("webhook_id","shopify_fulfillment_id","fulfillment_id","status")}.items():
        for column in columns: op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    raise RuntimeError("Fulfillment audit data is retained; deploy the prior application to roll back")
