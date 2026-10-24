"""Add durable leases for Shopify synchronization workers.

Revision ID: 20261021_04
Revises: 20261021_03
"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_04"
down_revision = "20261021_03"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("shopify_sync_events")
    if "claim_token" not in columns:
        op.add_column("shopify_sync_events", sa.Column("claim_token", sa.String(64)))
    if "claim_expires_at" not in columns:
        op.add_column("shopify_sync_events", sa.Column("claim_expires_at", sa.DateTime()))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("shopify_sync_events")}
    if "ix_shopify_sync_events_claim_token" not in indexes:
        op.create_index("ix_shopify_sync_events_claim_token", "shopify_sync_events", ["claim_token"])
    if "ix_shopify_sync_events_claim_expires_at" not in indexes:
        op.create_index("ix_shopify_sync_events_claim_expires_at", "shopify_sync_events",
                        ["claim_expires_at"])


def downgrade():
    raise RuntimeError("Shopify synchronization leases are retained; deploy the prior app to roll back")
