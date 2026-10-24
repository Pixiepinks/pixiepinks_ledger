"""Add durable Shopify payment synchronization operations.

Revision ID: 20261024_07
Revises: 20261024_06
"""
from alembic import op
import sqlalchemy as sa

revision = "20261024_07"
down_revision = "20261024_06"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "shopify_payment_sync_operations" not in inspector.get_table_names():
        op.create_table(
            "shopify_payment_sync_operations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("operation_id", sa.String(36), nullable=False),
            sa.Column("idempotency_key", sa.String(100), nullable=False),
            sa.Column("sales_order_id", sa.Integer(), sa.ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("shopify_order_id", sa.String(255), nullable=False),
            sa.Column("active_shopify_order_id", sa.String(255)),
            sa.Column("mutation_name", sa.String(80), nullable=False, server_default="orderMarkAsPaid"),
            sa.Column("intended_amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("currency", sa.String(8), nullable=False),
            sa.Column("ledger_payment_fingerprint", sa.String(64), nullable=False),
            sa.Column("preflight_financial_status", sa.String(40), nullable=False),
            sa.Column("preflight_shopify_outstanding", sa.Numeric(14, 2), nullable=False),
            sa.Column("preflight_transaction_fingerprint", sa.String(64), nullable=False),
            sa.Column("status", sa.String(40), nullable=False),
            sa.Column("claim_token", sa.String(64)),
            sa.Column("claim_expires_at", sa.DateTime()),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("shopify_transaction_id", sa.String(255)),
            sa.Column("error_summary", sa.String(500)),
            sa.Column("approved_by", sa.String(100)),
            sa.Column("approved_at", sa.DateTime()),
            sa.Column("preflight_expires_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime()),
            sa.UniqueConstraint("operation_id", name="uq_shopify_payment_sync_operation_id"),
            sa.UniqueConstraint("idempotency_key", name="uq_shopify_payment_sync_idempotency"),
            sa.UniqueConstraint("active_shopify_order_id", name="uq_shopify_payment_sync_active_order"),
        )
        for col in ("sales_order_id", "shopify_order_id", "active_shopify_order_id", "status", "claim_token", "claim_expires_at", "shopify_transaction_id", "preflight_expires_at", "created_at"):
            op.create_index(f"ix_shopify_payment_sync_operations_{col}", "shopify_payment_sync_operations", [col])
    inspector = sa.inspect(op.get_bind())
    if "shopify_payment_sync_audit_events" not in inspector.get_table_names():
        op.create_table(
            "shopify_payment_sync_audit_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("operation_id", sa.Integer(), sa.ForeignKey("shopify_payment_sync_operations.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("action", sa.String(50), nullable=False),
            sa.Column("actor", sa.String(100), nullable=False),
            sa.Column("detail", sa.String(500)),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        for col in ("operation_id", "action", "created_at"):
            op.create_index(f"ix_shopify_payment_sync_audit_events_{col}", "shopify_payment_sync_audit_events", [col])


def downgrade():
    raise RuntimeError("Shopify payment synchronization audit data is retained; deploy the prior application to roll back")
