"""Add payment claims, audit, and Shopify financial observations.

Revision ID: 20261024_06
Revises: 20261023_05
"""
from alembic import op
import sqlalchemy as sa

revision = "20261024_06"
down_revision = "20261023_05"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    order_columns = _columns("sales_orders")
    order_additions = {
        "shopify_financial_status": sa.Column("shopify_financial_status", sa.String(40)),
        "shopify_payment_status_refreshed_at": sa.Column("shopify_payment_status_refreshed_at", sa.DateTime()),
        "shopify_payment_reconciliation_status": sa.Column(
            "shopify_payment_reconciliation_status", sa.String(40), nullable=False,
            server_default="NOT_CHECKED"),
        "shopify_payment_reconciliation_warning": sa.Column(
            "shopify_payment_reconciliation_warning", sa.String(500)),
        "shopify_total_received": sa.Column("shopify_total_received", sa.Numeric(14, 2)),
        "shopify_total_outstanding": sa.Column("shopify_total_outstanding", sa.Numeric(14, 2)),
        "shopify_total_refunded": sa.Column("shopify_total_refunded", sa.Numeric(14, 2)),
        "shopify_payment_currency": sa.Column("shopify_payment_currency", sa.String(8)),
    }
    for name, column in order_additions.items():
        if name not in order_columns:
            op.add_column("sales_orders", column)

    payment_columns = _columns("sales_payments")
    payment_additions = {
        "idempotency_key": sa.Column("idempotency_key", sa.String(100)),
        "request_payload_hash": sa.Column("request_payload_hash", sa.String(64)),
        "deduplication_key": sa.Column("deduplication_key", sa.String(255)),
        "currency": sa.Column("currency", sa.String(8), nullable=False, server_default="LKR"),
        "source": sa.Column("source", sa.String(40), nullable=False, server_default="STAFF_RECORDED"),
        "claimed_by": sa.Column("claimed_by", sa.String(100)),
        "claimed_at": sa.Column("claimed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        "rejected_by": sa.Column("rejected_by", sa.String(100)),
        "rejected_at": sa.Column("rejected_at", sa.DateTime()),
        "rejection_reason": sa.Column("rejection_reason", sa.Text()),
        "updated_at": sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    }
    for name, column in payment_additions.items():
        if name not in payment_columns:
            op.add_column("sales_payments", column)

    inspector = sa.inspect(op.get_bind())
    payment_indexes = {index["name"] for index in inspector.get_indexes("sales_payments")}
    for name in ("idempotency_key", "deduplication_key"):
        index_name = f"uq_sales_payments_{name}"
        if index_name not in payment_indexes:
            op.create_index(index_name, "sales_payments", [name], unique=True)
    if "ix_sales_payments_order_verification" not in payment_indexes:
        op.create_index("ix_sales_payments_order_verification", "sales_payments",
                        ["sales_order_id", "verification_status"])
    order_indexes = {index["name"] for index in inspector.get_indexes("sales_orders")}
    for name in ("shopify_financial_status", "shopify_payment_reconciliation_status"):
        index_name = f"ix_sales_orders_{name}"
        if index_name not in order_indexes:
            op.create_index(index_name, "sales_orders", [name])

    if "sales_payment_audit_events" not in inspector.get_table_names():
        op.create_table(
            "sales_payment_audit_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("sales_payment_id", sa.Integer(),
                      sa.ForeignKey("sales_payments.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("sales_order_id", sa.Integer(),
                      sa.ForeignKey("sales_orders.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("action", sa.String(40), nullable=False),
            sa.Column("previous_status", sa.String(40)),
            sa.Column("new_status", sa.String(40), nullable=False),
            sa.Column("actor", sa.String(100), nullable=False),
            sa.Column("reason", sa.Text()),
            sa.Column("idempotency_key", sa.String(100), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("idempotency_key", name="uq_sales_payment_audit_idempotency"),
        )
        for column in ("sales_payment_id", "sales_order_id", "action", "idempotency_key", "created_at"):
            op.create_index(f"ix_sales_payment_audit_events_{column}",
                            "sales_payment_audit_events", [column])


def downgrade():
    raise RuntimeError("Payment audit data is retained; deploy the prior application to roll back")
