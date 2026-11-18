"""Durable reviewed barcode sync; preserves all existing data on rollback."""
from alembic import op
revision='20261118_43'
down_revision='20261117_42'
branch_labels=None
depends_on=None


def upgrade():
    from retail_models import RetailBarcodeJob,RetailBarcodeItem,RetailBarcodeReservation,RetailBarcodeEvent
    connection=op.get_bind()
    for model in [RetailBarcodeJob,RetailBarcodeItem,RetailBarcodeReservation,RetailBarcodeEvent]:
        model.__table__.create(connection,checkfirst=True)
    if connection.dialect.name=='sqlite':
        for action in ['UPDATE','DELETE']:
            connection.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS retail_barcode_events_no_{action.lower()} BEFORE {action} ON retail_barcode_events BEGIN SELECT RAISE(ABORT, 'retail_immutable_record'); END")
    elif connection.dialect.name=='postgresql':
        connection.exec_driver_sql("CREATE OR REPLACE FUNCTION retail_barcode_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'retail_immutable_record'; END $$")
        connection.exec_driver_sql("""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='retail_barcode_events_immutable' AND tgrelid='retail_barcode_events'::regclass) THEN
                CREATE TRIGGER retail_barcode_events_immutable BEFORE UPDATE OR DELETE ON retail_barcode_events FOR EACH ROW EXECUTE FUNCTION retail_barcode_reject_change();
            END IF;
        END $$""")

    # Freeze reviewed identity/state independently of mutable execution status.
    if connection.dialect.name=='sqlite':
        connection.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS retail_barcode_item_snapshot BEFORE UPDATE ON retail_barcode_items
            WHEN NEW.snapshot IS NOT OLD.snapshot OR NEW.before IS NOT OLD.before OR NEW.variant_id IS NOT OLD.variant_id
            OR NEW.job_id IS NOT OLD.job_id OR NEW.idempotency_key IS NOT OLD.idempotency_key
            BEGIN SELECT RAISE(ABORT, 'retail_immutable_snapshot'); END""")
    elif connection.dialect.name=='postgresql':
        connection.exec_driver_sql("""CREATE OR REPLACE FUNCTION retail_barcode_freeze_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF OLD.snapshot::text IS DISTINCT FROM NEW.snapshot::text OR OLD.before::text IS DISTINCT FROM NEW.before::text
                OR OLD.variant_id IS DISTINCT FROM NEW.variant_id OR OLD.job_id IS DISTINCT FROM NEW.job_id
                OR OLD.idempotency_key IS DISTINCT FROM NEW.idempotency_key THEN RAISE EXCEPTION 'retail_immutable_snapshot'; END IF;
            RETURN NEW; END $$""")
        connection.exec_driver_sql("""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='retail_barcode_item_snapshot' AND tgrelid='retail_barcode_items'::regclass) THEN
                CREATE TRIGGER retail_barcode_item_snapshot BEFORE UPDATE ON retail_barcode_items FOR EACH ROW EXECUTE FUNCTION retail_barcode_freeze_snapshot();
            END IF;
        END $$""")


def downgrade():
    pass  # Keep approvals, uncertain outcomes, reservations and event history for reconciliation.
