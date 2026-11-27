"""Private Monara voice assets, approval policy and immutable audit. No external calls."""
from alembic import op
import sqlalchemy as sa
revision='20261127_62'
down_revision='20261126_61'
branch_labels=depends_on=None
def upgrade():
    from monara_models import MonaraPolicy,MonaraJob,MonaraSend,MonaraEvent
    bind=op.get_bind()
    for m in (MonaraPolicy,MonaraJob,MonaraSend,MonaraEvent):m.__table__.create(bind,checkfirst=True)
    if not bind.execute(sa.text('SELECT 1 FROM monara_policy WHERE id=1')).first():bind.execute(sa.text("INSERT INTO monara_policy (id,version,data) VALUES (1,1,'{}')"))
    if bind.dialect.name=='sqlite':
        for action in ('UPDATE','DELETE'):bind.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS monara_events_no_{action.lower()} BEFORE {action} ON monara_events BEGIN SELECT RAISE(ABORT,'monara_audit_immutable'); END")
    else:
        bind.exec_driver_sql("CREATE OR REPLACE FUNCTION monara_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'monara_audit_immutable'; END $$")
        bind.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='monara_audit_immutable' AND tgrelid='monara_events'::regclass) THEN CREATE TRIGGER monara_audit_immutable BEFORE UPDATE OR DELETE ON monara_events FOR EACH ROW EXECUTE FUNCTION monara_audit_immutable(); END IF; END $$")
def downgrade():
    bind=op.get_bind()
    if any(bind.execute(sa.text('SELECT 1 FROM '+t+' LIMIT 1')).first() for t in ('monara_jobs','monara_sends','monara_events')):raise RuntimeError('Monara history exists; retain schema and disable gates for code rollback')
    for t in ('monara_events','monara_sends','monara_jobs','monara_policy'):op.drop_table(t)
    if bind.dialect.name=='postgresql':bind.exec_driver_sql('DROP FUNCTION IF EXISTS monara_audit_immutable()')
