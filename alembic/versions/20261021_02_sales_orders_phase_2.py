"""Add product presentation snapshots for Phase 2 order screens.

Revision ID: 20261021_02
Revises: 20261021_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20261021_02"
down_revision = "20261021_01"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("sales_order_lines")}
    if "product_image_url" not in columns:
        op.add_column("sales_order_lines", sa.Column("product_image_url", sa.Text()))
    if "product_url" not in columns:
        op.add_column("sales_order_lines", sa.Column("product_url", sa.Text()))


def downgrade():
    raise RuntimeError("Phase 2 downgrade is intentionally non-destructive; disable SALES_ORDERS_ENABLED instead")
