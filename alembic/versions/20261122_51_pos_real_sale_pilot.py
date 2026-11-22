"""Add immutable explicit pilot scope and cart approvals; no existing row updates."""
from alembic import op
revision='20261122_51'
down_revision='20261122_50'
branch_labels=None
depends_on=None


def upgrade():
    from pos_models import POSPilotScope,POSPilotAuthorization
    conn=op.get_bind()
    for model in [POSPilotScope,POSPilotAuthorization]:
        model.__table__.create(conn,checkfirst=True);name=model.__tablename__
        if conn.dialect.name=='sqlite':
            for action in ['UPDATE','DELETE']:
                conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS {name}_no_{action} BEFORE {action} ON {name} BEGIN SELECT RAISE(ABORT,'pos_pilot_approval_immutable'); END")
        elif conn.dialect.name=='postgresql':
            conn.exec_driver_sql("CREATE OR REPLACE FUNCTION pos_pilot_immutable_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'pos_pilot_approval_immutable'; END $$")
            conn.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='{name}_immutable' AND tgrelid='{name}'::regclass) THEN CREATE TRIGGER {name}_immutable BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION pos_pilot_immutable_fn(); END IF; END $$")


def downgrade():pass  # Preserve real-sale authorizations, identities and audit links.
