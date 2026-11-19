"""Add integrated POS orchestration, registers and immutable financial audit boundaries."""
from alembic import op
import sqlalchemy as sa
revision='20261119_47'
down_revision='20261119_46'
branch_labels=None
depends_on=None


def upgrade():
    from pos_models import POSConfig,POSRegister,POSSession,POSCart,POSCheckout,POSOperation,POSRefund,POSCashMovement,POSAudit,POSPosting,POSCustomerBinding
    conn=op.get_bind()
    if 'sales_channel' not in {c['name'] for c in sa.inspect(conn).get_columns('sales_orders')}:
        op.add_column('sales_orders',sa.Column('sales_channel',sa.String(20)))
    for model in [POSConfig,POSRegister,POSSession,POSCart,POSCheckout,POSOperation,POSRefund,POSCashMovement,POSAudit,POSPosting,POSCustomerBinding]:model.__table__.create(conn,checkfirst=True)
    for table in ['pos_audits','pos_cash_movements','pos_postings','pos_customer_bindings']:
        if conn.dialect.name=='sqlite':
            for action in ['UPDATE','DELETE']:conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'pos_history_immutable'); END")
        elif conn.dialect.name=='postgresql':
            conn.exec_driver_sql("CREATE OR REPLACE FUNCTION pos_history_immutable_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'pos_history_immutable'; END $$")
            conn.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='{table}_immutable' AND tgrelid='{table}'::regclass) THEN CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION pos_history_immutable_fn(); END IF; END $$")
    fences={
      'pos_checkouts':['shop','cart_id','sales_order_id','reference','request_key','actor_id','session_id','frozen','frozen_hash'],
      'pos_operations':['shop','checkout_id','kind','business_key','actor_id','plan'],
      'pos_refunds':['checkout_id','operation_id','session_id','plan','approved_by'],
      'pos_registers':['shop','location_id'],
      'pos_sessions':['shop','register_id','cashier_id','opening_float','opened_at'],
    }
    for table,columns in fences.items():
        if conn.dialect.name=='sqlite':
            checks=' OR '.join(f'NEW.{k} IS NOT OLD.{k}' for k in columns)
            if table=='pos_operations':checks+=' OR NEW.attempts < OLD.attempts OR NEW.attempts > 1 OR (OLD.wire IS NOT NULL AND NEW.wire IS NOT OLD.wire) OR (OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS NOT OLD.dispatched_at)'
            conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS {table}_fence BEFORE UPDATE ON {table} WHEN {checks} BEGIN SELECT RAISE(ABORT,'pos_snapshot_immutable'); END")
            conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS {table}_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'pos_history_immutable'); END")
        elif conn.dialect.name=='postgresql':
            checks=' OR '.join(f'NEW.{k}::text IS DISTINCT FROM OLD.{k}::text' for k in columns)
            if table=='pos_operations':checks+=' OR NEW.attempts < OLD.attempts OR NEW.attempts > 1 OR (OLD.wire IS NOT NULL AND NEW.wire::text IS DISTINCT FROM OLD.wire::text) OR (OLD.dispatched_at IS NOT NULL AND NEW.dispatched_at IS DISTINCT FROM OLD.dispatched_at)'
            conn.exec_driver_sql(f"CREATE OR REPLACE FUNCTION {table}_fence_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP='DELETE' THEN RAISE EXCEPTION 'pos_history_immutable'; END IF; IF {checks} THEN RAISE EXCEPTION 'pos_snapshot_immutable'; END IF; RETURN NEW; END $$")
            conn.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='{table}_fence' AND tgrelid='{table}'::regclass) THEN CREATE TRIGGER {table}_fence BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION {table}_fence_fn(); END IF; END $$")

    frozen=['sales_channel','customer_id','customer_name','whatsapp_phone','subtotal','discount_total','shipping_total','service_total','tax_total','grand_total','currency']
    if conn.dialect.name=='sqlite':
        changed=' OR '.join(f'NEW.{k} IS NOT OLD.{k}' for k in frozen)
        conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS pos_order_prices_frozen BEFORE UPDATE ON sales_orders WHEN OLD.sales_channel='POS' AND ({changed}) BEGIN SELECT RAISE(ABORT,'pos_price_snapshot_immutable'); END")
        for action in ['UPDATE','DELETE']:
            conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS pos_line_{action}_frozen BEFORE {action} ON sales_order_lines WHEN EXISTS (SELECT 1 FROM sales_orders WHERE id=OLD.sales_order_id AND sales_channel='POS') BEGIN SELECT RAISE(ABORT,'pos_price_snapshot_immutable'); END")
        conn.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS pos_line_insert_frozen BEFORE INSERT ON sales_order_lines WHEN EXISTS (SELECT 1 FROM pos_checkouts WHERE sales_order_id=NEW.sales_order_id) BEGIN SELECT RAISE(ABORT,'pos_price_snapshot_immutable'); END")
    elif conn.dialect.name=='postgresql':
        changed=' OR '.join(f'NEW.{k}::text IS DISTINCT FROM OLD.{k}::text' for k in frozen)
        conn.exec_driver_sql(f"CREATE OR REPLACE FUNCTION pos_order_prices_frozen_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.sales_channel='POS' AND ({changed}) THEN RAISE EXCEPTION 'pos_price_snapshot_immutable'; END IF; RETURN NEW; END $$")
        conn.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='pos_order_prices_frozen' AND tgrelid='sales_orders'::regclass) THEN CREATE TRIGGER pos_order_prices_frozen BEFORE UPDATE ON sales_orders FOR EACH ROW EXECUTE FUNCTION pos_order_prices_frozen_fn(); END IF; END $$")
        conn.exec_driver_sql("""CREATE OR REPLACE FUNCTION pos_line_frozen_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF TG_OP='INSERT' THEN IF EXISTS(SELECT 1 FROM pos_checkouts WHERE sales_order_id=NEW.sales_order_id) THEN RAISE EXCEPTION 'pos_price_snapshot_immutable'; END IF; RETURN NEW; END IF;
            IF EXISTS(SELECT 1 FROM sales_orders WHERE id=OLD.sales_order_id AND sales_channel='POS') THEN RAISE EXCEPTION 'pos_price_snapshot_immutable'; END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$""")
        conn.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='pos_line_frozen' AND tgrelid='sales_order_lines'::regclass) THEN CREATE TRIGGER pos_line_frozen BEFORE INSERT OR UPDATE OR DELETE ON sales_order_lines FOR EACH ROW EXECUTE FUNCTION pos_line_frozen_fn(); END IF; END $$")

    payment_fields=['sales_order_id','payment_method','payment_reference','amount','currency','payment_date','source','idempotency_key','request_payload_hash','deduplication_key']
    if conn.dialect.name=='sqlite':
        changed=' OR '.join(f'NEW.{k} IS NOT OLD.{k}' for k in payment_fields)
        conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS pos_tenders_frozen BEFORE UPDATE ON sales_payments WHEN OLD.source='POS' AND ({changed} OR (OLD.accounting_entry_id IS NOT NULL AND NEW.accounting_entry_id IS NOT OLD.accounting_entry_id)) BEGIN SELECT RAISE(ABORT,'pos_tender_immutable'); END")
        for table,key in [('journal_entries','id'),('journal_lines','entry_id')]:
            for action in ['UPDATE','DELETE']:
                conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS pos_{table}_{action} BEFORE {action} ON {table} WHEN EXISTS(SELECT 1 FROM pos_postings WHERE entry_id=OLD.{key}) BEGIN SELECT RAISE(ABORT,'pos_journal_immutable'); END")
        conn.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS pos_journal_line_insert BEFORE INSERT ON journal_lines WHEN EXISTS(SELECT 1 FROM pos_postings WHERE entry_id=NEW.entry_id) BEGIN SELECT RAISE(ABORT,'pos_journal_immutable'); END")
    elif conn.dialect.name=='postgresql':
        changed=' OR '.join(f'NEW.{k}::text IS DISTINCT FROM OLD.{k}::text' for k in payment_fields)
        conn.exec_driver_sql(f"CREATE OR REPLACE FUNCTION pos_tenders_frozen_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.source='POS' AND ({changed} OR (OLD.accounting_entry_id IS NOT NULL AND NEW.accounting_entry_id IS DISTINCT FROM OLD.accounting_entry_id)) THEN RAISE EXCEPTION 'pos_tender_immutable'; END IF; RETURN NEW; END $$")
        conn.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='pos_tenders_frozen' AND tgrelid='sales_payments'::regclass) THEN CREATE TRIGGER pos_tenders_frozen BEFORE UPDATE ON sales_payments FOR EACH ROW EXECUTE FUNCTION pos_tenders_frozen_fn(); END IF; END $$")
        for table,key in [('journal_entries','id'),('journal_lines','entry_id')]:
            conn.exec_driver_sql(f"CREATE OR REPLACE FUNCTION pos_{table}_immutable_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF EXISTS(SELECT 1 FROM pos_postings WHERE entry_id=OLD.{key}) THEN RAISE EXCEPTION 'pos_journal_immutable'; END IF; IF TG_OP='DELETE' THEN RETURN OLD; END IF; RETURN NEW; END $$")
            conn.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='pos_{table}_immutable' AND tgrelid='{table}'::regclass) THEN CREATE TRIGGER pos_{table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION pos_{table}_immutable_fn(); END IF; END $$")
        conn.exec_driver_sql("CREATE OR REPLACE FUNCTION pos_journal_insert_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF EXISTS(SELECT 1 FROM pos_postings WHERE entry_id=NEW.entry_id) THEN RAISE EXCEPTION 'pos_journal_immutable'; END IF; RETURN NEW; END $$")
        conn.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='pos_journal_insert' AND tgrelid='journal_lines'::regclass) THEN CREATE TRIGGER pos_journal_insert BEFORE INSERT ON journal_lines FOR EACH ROW EXECUTE FUNCTION pos_journal_insert_fn(); END IF; END $$")

    # Closing and canonical tender/order records remain financial evidence.
    if conn.dialect.name=='sqlite':
        conn.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS pos_closing_frozen BEFORE UPDATE ON pos_sessions WHEN OLD.status='CLOSED' BEGIN SELECT RAISE(ABORT,'pos_closing_immutable'); END")
        for table,condition in [('sales_orders',"OLD.sales_channel='POS'"),('sales_payments',"OLD.source='POS'")]:
            conn.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS pos_{table}_delete BEFORE DELETE ON {table} WHEN {condition} BEGIN SELECT RAISE(ABORT,'pos_history_immutable'); END")
    elif conn.dialect.name=='postgresql':
        conn.exec_driver_sql("CREATE OR REPLACE FUNCTION pos_closing_frozen_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.status='CLOSED' THEN RAISE EXCEPTION 'pos_closing_immutable'; END IF; RETURN NEW; END $$")
        conn.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='pos_closing_frozen' AND tgrelid='pos_sessions'::regclass) THEN CREATE TRIGGER pos_closing_frozen BEFORE UPDATE ON pos_sessions FOR EACH ROW EXECUTE FUNCTION pos_closing_frozen_fn(); END IF; END $$")
        for table,condition in [('sales_orders',"OLD.sales_channel='POS'"),('sales_payments',"OLD.source='POS'")]:
            conn.exec_driver_sql(f"CREATE OR REPLACE FUNCTION pos_{table}_delete_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {condition} THEN RAISE EXCEPTION 'pos_history_immutable'; END IF; RETURN OLD; END $$")
            conn.exec_driver_sql(f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='pos_{table}_delete' AND tgrelid='{table}'::regclass) THEN CREATE TRIGGER pos_{table}_delete BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION pos_{table}_delete_fn(); END IF; END $$")


def downgrade():pass  # Never delete financial evidence or clear attempt fences.
