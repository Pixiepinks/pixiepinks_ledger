"""Add operational Shopify draft synchronization metadata.

Revision ID: 20261021_03
Revises: 20261021_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_03"
down_revision = "20261021_02"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    order_columns = _columns("sales_orders")
    additions = {
        "shopify_draft_order_name": sa.Column("shopify_draft_order_name", sa.String(100)),
        "shopify_sync_updated_at": sa.Column("shopify_sync_updated_at", sa.DateTime()),
        "shopify_sync_error": sa.Column("shopify_sync_error", sa.String(500)),
    }
    for name, column in additions.items():
        if name not in order_columns:
            op.add_column("sales_orders", column)
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("sales_orders")}
    if "ix_sales_orders_shopify_draft_order_name" not in indexes:
        op.create_index("ix_sales_orders_shopify_draft_order_name", "sales_orders",
                        ["shopify_draft_order_name"])

    event_columns = _columns("shopify_sync_events")
    if "actor" not in event_columns:
        op.add_column("shopify_sync_events", sa.Column("actor", sa.String(100)))
    if "updated_at" not in event_columns:
        op.add_column("shopify_sync_events", sa.Column("updated_at", sa.DateTime(),
                      nullable=False, server_default=sa.func.now()))


def downgrade():
    raise RuntimeError("Shopify synchronization history is retained; deploy the prior app to roll back")
