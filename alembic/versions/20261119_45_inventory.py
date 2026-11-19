"""Add inventory documents, counting observations, policies and immutable audit fences."""
from alembic import op
revision = '20261119_45'
down_revision = '20261119_44'
branch_labels = None
depends_on = None


def upgrade():
    from inventory_models import InventoryDocument, InventoryEvent, InventoryObservation, InventoryPolicy, InventoryPolicyEvent
    conn = op.get_bind()
    for model in [InventoryDocument, InventoryEvent, InventoryObservation, InventoryPolicy, InventoryPolicyEvent]:
        model.__table__.create(conn, checkfirst=True)
    for table in ['inventory_events','inventory_count_observations','inventory_policy_events']:
        if conn.dialect.name == 'sqlite':
            for action in ['UPDATE','DELETE']:
                conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS {table}_immutable_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'inventory_history_immutable'); END")
        elif conn.dialect.name == 'postgresql':
            conn.exec_driver_sql("CREATE OR REPLACE FUNCTION inventory_immutable_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'inventory_history_immutable'; END $$")
            conn.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='{table}_immutable' AND tgrelid='{table}'::regclass) THEN CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION inventory_immutable_fn(); END IF; END $$")
    # Input identity never changes. Baselines/plans/approval/dispatch intents can only be set once.
    if conn.dialect.name == 'sqlite':
        conn.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS inventory_document_fence BEFORE UPDATE ON inventory_documents
          WHEN NEW.shop IS NOT OLD.shop OR NEW.client_key IS NOT OLD.client_key OR NEW.kind IS NOT OLD.kind
          OR NEW.business_key IS NOT OLD.business_key OR NEW.reference IS NOT OLD.reference OR NEW.payload IS NOT OLD.payload OR NEW.payload_hash IS NOT OLD.payload_hash
          OR NEW.actor_id IS NOT OLD.actor_id OR NEW.dispatch_key IS NOT OLD.dispatch_key
          OR (OLD.baseline IS NOT NULL AND NEW.baseline IS NOT OLD.baseline)
          OR (OLD.plan IS NOT NULL AND NEW.plan IS NOT OLD.plan)
          OR (OLD.approval_hash IS NOT NULL AND NEW.approval_hash IS NOT OLD.approval_hash)
          OR (OLD.approved_by IS NOT NULL AND NEW.approved_by IS NOT OLD.approved_by)
          OR NEW.attempts < OLD.attempts OR NEW.attempts > 1
          OR (OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS NOT OLD.dispatched_at)
          BEGIN SELECT RAISE(ABORT,'inventory_document_immutable'); END""")
        conn.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS inventory_document_no_delete BEFORE DELETE ON inventory_documents BEGIN SELECT RAISE(ABORT,'inventory_history_immutable'); END")
    elif conn.dialect.name == 'postgresql':
        conn.exec_driver_sql("""CREATE OR REPLACE FUNCTION inventory_document_fence_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'inventory_history_immutable'; END IF;
          IF NEW.shop IS DISTINCT FROM OLD.shop OR NEW.client_key IS DISTINCT FROM OLD.client_key OR NEW.kind IS DISTINCT FROM OLD.kind
          OR NEW.business_key IS DISTINCT FROM OLD.business_key OR NEW.reference IS DISTINCT FROM OLD.reference OR NEW.payload::text IS DISTINCT FROM OLD.payload::text
          OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
          OR NEW.dispatch_key IS DISTINCT FROM OLD.dispatch_key
          OR (OLD.baseline IS NOT NULL AND NEW.baseline::text IS DISTINCT FROM OLD.baseline::text)
          OR (OLD.plan IS NOT NULL AND NEW.plan::text IS DISTINCT FROM OLD.plan::text)
          OR (OLD.approval_hash IS NOT NULL AND NEW.approval_hash IS DISTINCT FROM OLD.approval_hash)
          OR (OLD.approved_by IS NOT NULL AND NEW.approved_by IS DISTINCT FROM OLD.approved_by)
          OR NEW.attempts < OLD.attempts OR NEW.attempts > 1
          OR (OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS DISTINCT FROM OLD.dispatched_at)
          THEN RAISE EXCEPTION 'inventory_document_immutable'; END IF; RETURN NEW; END $$""")
        conn.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='inventory_document_fence' AND tgrelid='inventory_documents'::regclass) THEN CREATE TRIGGER inventory_document_fence BEFORE UPDATE OR DELETE ON inventory_documents FOR EACH ROW EXECUTE FUNCTION inventory_document_fence_fn(); END IF; END $$")


def downgrade():
    pass  # Preserve immutable transaction and reconciliation history.
