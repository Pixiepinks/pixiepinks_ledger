"""Add downstream device, print/drawer intent and append-only hardware audit."""
from alembic import op
revision='20261120_48'
down_revision='20261119_47'
branch_labels=None
depends_on=None

def upgrade():
    from hardware_models import PrintDevice,HardwareJob,HardwareEvent,HardwareRate
    conn=op.get_bind()
    for model in [PrintDevice,HardwareJob,HardwareEvent,HardwareRate]:model.__table__.create(conn,checkfirst=True)
    immutable=['shop','device_id','register_id','location_id','device_version','business_key','kind','queue','media','actor_id','checkout_id','session_id','label_id','parent_id','detail','sha256','size','created_at','expires_at']
    if conn.dialect.name=='sqlite':
        changed=' OR '.join(f'NEW.{k} IS NOT OLD.{k}' for k in immutable)
        conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS hardware_jobs_frozen BEFORE UPDATE ON hardware_jobs WHEN {changed} OR NEW.attempts<OLD.attempts OR NEW.attempts>1 OR (OLD.claim_hash IS NOT NULL AND NEW.claim_hash IS NOT OLD.claim_hash) OR (OLD.permitted_at IS NOT NULL AND NEW.permitted_at IS NOT OLD.permitted_at) OR (NEW.pdf IS NOT OLD.pdf AND NEW.pdf IS NOT NULL) BEGIN SELECT RAISE(ABORT,'hardware_intent_immutable'); END")
        for action in ['UPDATE','DELETE']:conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS hardware_events_{action} BEFORE {action} ON hardware_events BEGIN SELECT RAISE(ABORT,'hardware_audit_immutable'); END")
        conn.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS hardware_jobs_delete BEFORE DELETE ON hardware_jobs BEGIN SELECT RAISE(ABORT,'hardware_intent_immutable'); END")
    elif conn.dialect.name=='postgresql':
        changed=' OR '.join(f'NEW.{k}::text IS DISTINCT FROM OLD.{k}::text' for k in immutable)
        conn.exec_driver_sql(f"CREATE OR REPLACE FUNCTION hardware_job_frozen_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP='DELETE' THEN RAISE EXCEPTION 'hardware_intent_immutable'; END IF; IF {changed} OR NEW.attempts<OLD.attempts OR NEW.attempts>1 OR (OLD.claim_hash IS NOT NULL AND NEW.claim_hash IS DISTINCT FROM OLD.claim_hash) OR (OLD.permitted_at IS NOT NULL AND NEW.permitted_at IS DISTINCT FROM OLD.permitted_at) OR (NEW.pdf IS DISTINCT FROM OLD.pdf AND NEW.pdf IS NOT NULL) THEN RAISE EXCEPTION 'hardware_intent_immutable'; END IF; RETURN NEW; END $$")
        conn.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='hardware_job_frozen' AND tgrelid='hardware_jobs'::regclass) THEN CREATE TRIGGER hardware_job_frozen BEFORE UPDATE OR DELETE ON hardware_jobs FOR EACH ROW EXECUTE FUNCTION hardware_job_frozen_fn(); END IF; END $$")
        conn.exec_driver_sql("CREATE OR REPLACE FUNCTION hardware_event_frozen_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'hardware_audit_immutable'; END $$")
        conn.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='hardware_event_frozen' AND tgrelid='hardware_events'::regclass) THEN CREATE TRIGGER hardware_event_frozen BEFORE UPDATE OR DELETE ON hardware_events FOR EACH ROW EXECUTE FUNCTION hardware_event_frozen_fn(); END IF; END $$")

def downgrade():pass  # Retain attempt fences and audit, including uncertain drawer actions.
