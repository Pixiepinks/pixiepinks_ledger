"""Fence pre-release jobs and support durable background previews; retain history."""
from alembic import op
import sqlalchemy as sa
revision='20261119_44'
down_revision='20261118_43'
branch_labels=None
depends_on=None


def upgrade():
    connection=op.get_bind()
    columns={c['name'] for c in sa.inspect(connection).get_columns('retail_barcode_jobs')}
    for column in [sa.Column('release_version',sa.String(40)),sa.Column('preview_selection',sa.JSON()),
        sa.Column('preview_capability',sa.JSON()),sa.Column('preview_claim',sa.String(40)),sa.Column('preview_lease',sa.DateTime())]:
        if column.name not in columns:op.add_column('retail_barcode_jobs',column)
    if 'ix_retail_barcode_jobs_release_version' not in {i['name'] for i in sa.inspect(connection).get_indexes('retail_barcode_jobs')}:
        op.create_index('ix_retail_barcode_jobs_release_version','retail_barcode_jobs',['release_version'])
    # No backfill: old approvals can NEVER acquire the new release identity.
    if connection.dialect.name=='sqlite':
        connection.exec_driver_sql("""CREATE TRIGGER IF NOT EXISTS retail_barcode_release_fence BEFORE UPDATE ON retail_barcode_jobs
          WHEN NEW.release_version IS NOT OLD.release_version OR NEW.preview_selection IS NOT OLD.preview_selection
          OR NEW.actor_id IS NOT OLD.actor_id OR NEW.token IS NOT OLD.token
          BEGIN SELECT RAISE(ABORT, 'retail_release_identity_immutable'); END""")
    elif connection.dialect.name=='postgresql':
        connection.exec_driver_sql("""CREATE OR REPLACE FUNCTION retail_barcode_release_fence_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
          IF NEW.release_version IS DISTINCT FROM OLD.release_version OR NEW.preview_selection::text IS DISTINCT FROM OLD.preview_selection::text
          OR NEW.actor_id IS DISTINCT FROM OLD.actor_id OR NEW.token IS DISTINCT FROM OLD.token THEN RAISE EXCEPTION 'retail_release_identity_immutable'; END IF;
          RETURN NEW; END $$""")
        connection.exec_driver_sql("""DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='retail_barcode_release_fence' AND tgrelid='retail_barcode_jobs'::regclass) THEN
          CREATE TRIGGER retail_barcode_release_fence BEFORE UPDATE ON retail_barcode_jobs FOR EACH ROW EXECUTE FUNCTION retail_barcode_release_fence_fn();
          END IF; END $$""")


def downgrade():
    pass  # Retain generation fences, snapshots, attempts and immutable audit history.
