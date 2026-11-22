"""Add register-level daily POS authorization and immutable receipt binding only."""
from alembic import op
revision='20261122_52'
down_revision='20261122_51'
branch_labels=None
depends_on=None


def upgrade():
    from pos_models import POSDailyPolicy,POSDailySale
    c=op.get_bind()
    for model in [POSDailyPolicy,POSDailySale]:
        model.__table__.create(c,checkfirst=True);name=model.__tablename__
        actions=['DELETE'] if model is POSDailyPolicy else ['UPDATE','DELETE']
        if c.dialect.name=='sqlite':
            for action in actions:c.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS {name}_no_{action} BEFORE {action} ON {name} BEGIN SELECT RAISE(ABORT,'daily_pos_history_immutable'); END")
        elif c.dialect.name=='postgresql':
            c.exec_driver_sql("CREATE OR REPLACE FUNCTION daily_pos_immutable_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'daily_pos_history_immutable'; END $$")
            events=' OR '.join(actions)
            c.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='{name}_immutable' AND tgrelid='{name}'::regclass) THEN CREATE TRIGGER {name}_immutable BEFORE {events} ON {name} FOR EACH ROW EXECUTE FUNCTION daily_pos_immutable_fn(); END IF; END $$")


def downgrade():pass  # Retain authorization/receipt identities; never erase financial evidence.
