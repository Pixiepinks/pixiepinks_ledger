"""Add purchasing subledger, immutable receiving links and append-only audit."""
from alembic import op
revision='20261122_53'
down_revision='20261122_52'
branch_labels=None
depends_on=None

def upgrade():
    from purchasing_models import PurchaseRecord,PurchaseMovement,PurchaseAudit
    c=op.get_bind()
    for model in (PurchaseRecord,PurchaseMovement,PurchaseAudit):model.__table__.create(c,checkfirst=True)
    immutable=('purchase_movements','purchase_audits')
    if c.dialect.name=='sqlite':
        for name in immutable:
            for action in ('UPDATE','DELETE'):
                c.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS {name}_no_{action} BEFORE {action} ON {name} BEGIN SELECT RAISE(ABORT,'purchase_history_immutable'); END")
        c.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS purchase_no_delete BEFORE DELETE ON purchase_records BEGIN SELECT RAISE(ABORT,'purchase_history_immutable'); END")
        c.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS purchase_frozen BEFORE UPDATE ON purchase_records WHEN
        NEW.id IS NOT OLD.id OR NEW.shop IS NOT OLD.shop OR NEW.kind IS NOT OLD.kind OR NEW.parent_id IS NOT OLD.parent_id OR NEW.supplier_id IS NOT OLD.supplier_id OR NEW.client_key IS NOT OLD.client_key OR NEW.business_key IS NOT OLD.business_key OR NEW.request_hash IS NOT OLD.request_hash OR NEW.actor_id IS NOT OLD.actor_id OR NEW.created_at IS NOT OLD.created_at OR
        (OLD.kind NOT IN ('SUPPLIER','LOCATION') AND ((NEW.payload IS NOT OLD.payload AND NOT (OLD.kind='BILL' AND OLD.status='DRAFT' AND NEW.status='DRAFT')) OR OLD.kind='PAYMENT'))
        BEGIN SELECT RAISE(ABORT,'purchase_record_frozen'); END""")
    elif c.dialect.name=='postgresql':
        c.exec_driver_sql("CREATE OR REPLACE FUNCTION purchase_immutable_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'purchase_history_immutable'; END $$")
        for name in immutable:
            c.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}_immutable ON {name}")
            c.exec_driver_sql(f"CREATE TRIGGER {name}_immutable BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION purchase_immutable_fn()")
        c.exec_driver_sql("""CREATE OR REPLACE FUNCTION purchase_frozen_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'purchase_history_immutable'; END IF;
        IF (NEW.id,NEW.shop,NEW.kind,NEW.parent_id,NEW.supplier_id,NEW.client_key,NEW.business_key,NEW.request_hash,NEW.actor_id,NEW.created_at) IS DISTINCT FROM
           (OLD.id,OLD.shop,OLD.kind,OLD.parent_id,OLD.supplier_id,OLD.client_key,OLD.business_key,OLD.request_hash,OLD.actor_id,OLD.created_at)
           OR (OLD.kind NOT IN ('SUPPLIER','LOCATION') AND ((NEW.payload::jsonb IS DISTINCT FROM OLD.payload::jsonb AND NOT (OLD.kind='BILL' AND OLD.status='DRAFT' AND NEW.status='DRAFT')) OR OLD.kind='PAYMENT'))
        THEN RAISE EXCEPTION 'purchase_record_frozen'; END IF; RETURN NEW; END $$""")
        c.exec_driver_sql("DROP TRIGGER IF EXISTS purchase_frozen ON purchase_records")
        c.exec_driver_sql("CREATE TRIGGER purchase_frozen BEFORE UPDATE OR DELETE ON purchase_records FOR EACH ROW EXECUTE FUNCTION purchase_frozen_fn()")

def downgrade():
    pass  # Preserve supplier payment and receiving evidence; rollback application with gates OFF.
