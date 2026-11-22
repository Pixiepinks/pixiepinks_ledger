"""Add isolated synthetic receipt checkouts; preserve every financial/hardware row."""
from alembic import op
revision='20261122_50'
down_revision='20261121_49'
branch_labels=None
depends_on=None


def upgrade():
    from hardware_models import POSReceiptTest
    conn=op.get_bind();POSReceiptTest.__table__.create(conn,checkfirst=True)
    frozen=['id','shop','request_key','device_id','device_version','actor_id','frozen','frozen_hash','created_at']
    final=['status','job_id','finalized_at']
    if conn.dialect.name=='sqlite':
        change=' OR '.join(f'NEW.{k} IS NOT OLD.{k}' for k in frozen)
        again=' OR '.join(f'NEW.{k} IS NOT OLD.{k}' for k in final)
        conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS pos_receipt_tests_frozen BEFORE UPDATE ON pos_receipt_tests WHEN {change} OR (OLD.status='FINALIZED' AND ({again})) BEGIN SELECT RAISE(ABORT,'test_checkout_immutable'); END")
        conn.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS pos_receipt_tests_delete BEFORE DELETE ON pos_receipt_tests BEGIN SELECT RAISE(ABORT,'test_checkout_immutable'); END")
    elif conn.dialect.name=='postgresql':
        change=' OR '.join(f'NEW.{k}::text IS DISTINCT FROM OLD.{k}::text' for k in frozen)
        again=' OR '.join(f'NEW.{k}::text IS DISTINCT FROM OLD.{k}::text' for k in final)
        conn.exec_driver_sql(f"CREATE OR REPLACE FUNCTION pos_receipt_test_frozen_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP='DELETE' THEN RAISE EXCEPTION 'test_checkout_immutable'; END IF; IF {change} OR (OLD.status='FINALIZED' AND ({again})) THEN RAISE EXCEPTION 'test_checkout_immutable'; END IF; RETURN NEW; END $$")
        conn.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='pos_receipt_test_frozen' AND tgrelid='pos_receipt_tests'::regclass) THEN CREATE TRIGGER pos_receipt_test_frozen BEFORE UPDATE OR DELETE ON pos_receipt_tests FOR EACH ROW EXECUTE FUNCTION pos_receipt_test_frozen_fn(); END IF; END $$")


def downgrade():pass  # Retain test identities/audit links and duplicate-prevention evidence.
