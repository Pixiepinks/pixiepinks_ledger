"""Additive retail product master, identifier ledger and label jobs; no live import."""
from alembic import op
revision='20261117_42'
down_revision='20261111_41'
branch_labels=None
depends_on=None

def upgrade():
    from retail_models import (RetailShop,RetailVariant,RetailSyncJob,RetailImportRow,RetailCounter,
        RetailIdentifier,RetailPreview,RetailAudit,RetailException,RetailLabelTemplate,RetailPrintJob)
    for model in [RetailShop,RetailVariant,RetailSyncJob,RetailImportRow,RetailCounter,RetailIdentifier,
                  RetailPreview,RetailAudit,RetailException,RetailLabelTemplate,RetailPrintJob]:
        model.__table__.create(op.get_bind(),checkfirst=True)
    # Allocation tombstones, sharing approvals and audits are append-only at the DB boundary.
    connection=op.get_bind()
    for table in ['retail_identifiers','retail_audits','retail_exceptions']:
        if connection.dialect.name=='sqlite':
            for action in ['UPDATE','DELETE']:
                connection.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'retail_immutable_record'); END")
        elif connection.dialect.name=='postgresql':
            connection.exec_driver_sql("CREATE OR REPLACE FUNCTION retail_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'retail_immutable_record'; END $$")
            connection.exec_driver_sql(f"""DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='{table}_immutable' AND tgrelid='{table}'::regclass) THEN
                    CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION retail_reject_change();
                END IF;
            END $$""")

def downgrade():
    pass  # Keep master records, committed identifiers and immutable audit history.
