"""Add Shopify order lifecycle linkage and webhook audit.

Revision ID: 20261023_05
Revises: 20261021_04
"""
from alembic import op
import sqlalchemy as sa

revision = "20261023_05"
down_revision = "20261021_04"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("sales_orders")
    additions = {
        "shopify_order_created_at": sa.Column("shopify_order_created_at", sa.DateTime()),
        "shopify_order_linked_at": sa.Column("shopify_order_linked_at", sa.DateTime()),
        "shopify_source_draft_order_id": sa.Column("shopify_source_draft_order_id", sa.String(255)),
        "shopify_lifecycle_status": sa.Column("shopify_lifecycle_status", sa.String(40),
                                               nullable=False, server_default="NOT_LINKED"),
        "shopify_lifecycle_synced_at": sa.Column("shopify_lifecycle_synced_at", sa.DateTime()),
        "shopify_lifecycle_error": sa.Column("shopify_lifecycle_error", sa.String(500)),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("sales_orders", column)
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("sales_orders")}
    for name in ("shopify_source_draft_order_id", "shopify_lifecycle_status"):
        index_name = f"ix_sales_orders_{name}"
        if index_name not in indexes:
            op.create_index(index_name, "sales_orders", [name])

    if "shopify_webhook_events" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("shopify_webhook_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("webhook_id", sa.String(255), nullable=False),
            sa.Column("topic", sa.String(100), nullable=False),
            sa.Column("shop_domain", sa.String(255), nullable=False),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.Column("shopify_order_id", sa.String(255)),
            sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id", ondelete="RESTRICT")),
            sa.Column("status", sa.String(40), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("claim_token", sa.String(64)),
            sa.Column("claim_expires_at", sa.DateTime()),
            sa.Column("error_summary", sa.String(500)),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("processed_at", sa.DateTime()),
            sa.UniqueConstraint("webhook_id", name="uq_shopify_webhook_events_webhook_id"))
        for column in ("webhook_id", "topic", "shop_domain", "shopify_order_id", "sales_order_id",
                       "status", "claim_token", "claim_expires_at", "created_at"):
            op.create_index(f"ix_shopify_webhook_events_{column}", "shopify_webhook_events", [column])


def downgrade():
    raise RuntimeError("Shopify lifecycle audit data is retained; deploy the prior app to roll back")
