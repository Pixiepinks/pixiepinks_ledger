"""Fence approvals to reviewed inventory semantics; preserve all earlier records."""
from alembic import op
import sqlalchemy as sa
revision='20261119_46'
down_revision='20261119_45'
branch_labels=None
depends_on=None


def upgrade():
    conn=op.get_bind()
    if 'contract_version' not in {c['name'] for c in sa.inspect(conn).get_columns('inventory_documents')}:
        op.add_column('inventory_documents',sa.Column('contract_version',sa.String(60)))
    # NULL remains NULL on all previous documents: no old approval acquires new semantics.
    if conn.dialect.name=='sqlite':
        conn.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS inventory_contract_immutable BEFORE UPDATE ON inventory_documents
          WHEN NEW.contract_version IS NOT OLD.contract_version
          BEGIN SELECT RAISE(ABORT,'inventory_contract_immutable'); END""")
    elif conn.dialect.name=='postgresql':
        conn.exec_driver_sql("""CREATE OR REPLACE FUNCTION inventory_contract_immutable_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.contract_version IS DISTINCT FROM OLD.contract_version THEN RAISE EXCEPTION 'inventory_contract_immutable'; END IF; RETURN NEW; END $$""")
        conn.exec_driver_sql("""DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='inventory_contract_immutable' AND tgrelid='inventory_documents'::regclass) THEN
          CREATE TRIGGER inventory_contract_immutable BEFORE UPDATE ON inventory_documents FOR EACH ROW EXECUTE FUNCTION inventory_contract_immutable_fn(); END IF; END $$""")


def downgrade():
    pass  # Keep immutable approval contract identity and audit evidence.
