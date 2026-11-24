"""Local historical evidence and explicitly approved customer reconciliation."""
from alembic import op
revision='20261124_56'
down_revision='20261124_55'
branch_labels=None
depends_on=None
NAMES=('LoyaltyHistoryScan','LoyaltyHistoricalOrder','LoyaltyRecoveryPlan')
def upgrade():
    import loyalty_models as m
    for name in NAMES:getattr(m,name).__table__.create(op.get_bind(),checkfirst=True)
def downgrade():
    import loyalty_models as m
    for name in reversed(NAMES):op.drop_table(getattr(m,name).__tablename__)
