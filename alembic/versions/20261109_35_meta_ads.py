"""Read-only Meta reporting cache and synchronization status."""
from alembic import op
from meta_ads_models import MetaAdsDaily, MetaAdsSync
revision = '20261109_35'
down_revision = '20261108_34'
branch_labels = None
depends_on = None


def upgrade():
    for table in (MetaAdsDaily.__table__, MetaAdsSync.__table__):
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    # Preserve reporting history for rollback/redeploy; old app ignores these tables.
    pass
