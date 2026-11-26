"""Additive sales opportunity, commitment audit and reminder queue."""
from alembic import op
import sqlalchemy as sa
revision='20261126_61'
down_revision='20261126_60'
branch_labels=depends_on=None

def upgrade():
    from sales_pipeline_models import SalesOpportunity,SalesPipelineEvent,SalesPipelineReminder,SalesPipelineControl
    for m in (SalesOpportunity,SalesPipelineEvent,SalesPipelineReminder,SalesPipelineControl):m.__table__.create(op.get_bind(),checkfirst=True)
    bind=op.get_bind()
    if bind.dialect.name=='sqlite':
        for action in ['UPDATE','DELETE']:
            bind.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS pipeline_audit_no_{action.lower()} BEFORE {action} ON sales_pipeline_events BEGIN SELECT RAISE(ABORT, 'pipeline_audit_immutable'); END")
    elif bind.dialect.name=='postgresql':
        bind.exec_driver_sql("CREATE OR REPLACE FUNCTION pipeline_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'pipeline_audit_immutable'; END $$")
        bind.exec_driver_sql("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='pipeline_audit_immutable' AND tgrelid='sales_pipeline_events'::regclass) THEN CREATE TRIGGER pipeline_audit_immutable BEFORE UPDATE OR DELETE ON sales_pipeline_events FOR EACH ROW EXECUTE FUNCTION pipeline_audit_immutable(); END IF; END $$")

def downgrade():
    bind=op.get_bind()
    if any(bind.execute(sa.text('SELECT 1 FROM '+name+' LIMIT 1')).first() for name in ['sales_opportunities','sales_pipeline_events','sales_pipeline_reminders','sales_pipeline_control']):
        raise RuntimeError('Pipeline history exists; retain additive schema and use compatible code rollback.')
    for name in ('sales_pipeline_reminders','sales_pipeline_events','sales_pipeline_control','sales_opportunities'):op.drop_table(name)

    if bind.dialect.name=='postgresql':bind.exec_driver_sql('DROP FUNCTION IF EXISTS pipeline_audit_immutable()')
