"""Add independent loyalty membership, pricing, audit and migration records."""
from alembic import op
import sqlalchemy as sa
revision='20261124_55'
down_revision='20261123_54'
branch_labels=None
depends_on=None

NAMES=('LoyaltyConfig','LoyaltyMember','LoyaltyRule','LoyaltyRuleVersion','LoyaltyPricing','LoyaltyRedemption','LoyaltyRedemptionEvent','LoyaltyAudit','LoyaltyIdentityReview','LoyaltyOrderIdentity','LoyaltyMigration','LoyaltyMigrationRow','LoyaltyRecalculation','LoyaltyOrderExclusion')
IMMUTABLE=('loyalty_audits','loyalty_rule_versions','loyalty_redemptions','loyalty_redemption_events','loyalty_order_identities','loyalty_order_exclusions')
def upgrade():
    import loyalty_models as m
    bind=op.get_bind()
    for name in NAMES:getattr(m,name).__table__.create(bind,checkfirst=True)
    if not bind.execute(sa.select(m.LoyaltyConfig.id)).first():
        bind.execute(m.LoyaltyConfig.__table__.insert().values(id=1,enabled=False,version=1,value={'name':'PixiePinks Loyalty Club','minimum_purchase':'0.01','minimum_margin':'20.00','missing_cost_policy':'BLOCK','combination_policy':'LOWER_PRICE_NO_STACK','default_days':30,'channels':['CRM']}))
    if bind.dialect.name=='sqlite':
        for table in IMMUTABLE:
            for action in ('UPDATE','DELETE'):bind.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'loyalty_history_immutable'); END")
        for action in ('UPDATE','DELETE'):bind.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS loyalty_pricing_no_{action} BEFORE {action} ON loyalty_order_pricing WHEN OLD.frozen=1 BEGIN SELECT RAISE(ABORT,'loyalty_pricing_frozen'); END")
    elif bind.dialect.name=='postgresql':
        bind.exec_driver_sql("CREATE OR REPLACE FUNCTION loyalty_immutable_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'loyalty_history_immutable'; END $$")
        for table in IMMUTABLE:
            bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}')
            bind.exec_driver_sql(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION loyalty_immutable_fn()')
        bind.exec_driver_sql("CREATE OR REPLACE FUNCTION loyalty_pricing_frozen_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.frozen THEN RAISE EXCEPTION 'loyalty_pricing_frozen'; END IF; IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$")
        bind.exec_driver_sql('DROP TRIGGER IF EXISTS loyalty_pricing_frozen ON loyalty_order_pricing')
        bind.exec_driver_sql('CREATE TRIGGER loyalty_pricing_frozen BEFORE UPDATE OR DELETE ON loyalty_order_pricing FOR EACH ROW EXECUTE FUNCTION loyalty_pricing_frozen_fn()')

def downgrade():
    import loyalty_models as m
    for name in reversed(NAMES):op.drop_table(getattr(m,name).__tablename__)
    if op.get_bind().dialect.name=='postgresql':
        op.execute('DROP FUNCTION IF EXISTS loyalty_pricing_frozen_fn()');op.execute('DROP FUNCTION IF EXISTS loyalty_immutable_fn()')
