"""Sanitized Meta sync stages and numeric request evidence."""
from alembic import op
import sqlalchemy as sa
revision = '20261109_36'
down_revision = '20261109_35'
branch_labels = None
depends_on = None


def upgrade():
    if 'diagnostics' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('meta_ads_sync')}:
        op.add_column('meta_ads_sync', sa.Column('diagnostics', sa.JSON(), nullable=True))


def downgrade():
    pass  # Retain additive evidence; previous application ignores it.
