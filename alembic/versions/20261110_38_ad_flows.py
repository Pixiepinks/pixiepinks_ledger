"""Ad-triggered saved question mappings, execution evidence and opt-out state."""
from alembic import op
import sqlalchemy as sa
revision='20261110_38'
down_revision='20261109_37'
branch_labels=None
depends_on=None


def upgrade():
    # These reporting/control tables have no alterations to the existing flow definitions/runs.
    from ad_flow_models import AdFlowControl,AdFlowRule,AdFlowExecution,AdFlowAudit
    bind=op.get_bind()
    for table in (AdFlowControl.__table__,AdFlowRule.__table__,AdFlowExecution.__table__,AdFlowAudit.__table__):
        table.create(bind,checkfirst=True)
    if not bind.execute(sa.select(AdFlowControl.__table__.c.id).where(AdFlowControl.__table__.c.id==1)).first():
        bind.execute(AdFlowControl.__table__.insert().values(id=1,enabled=False,version=0))
    if 'ad_flow_opted_out' not in {c['name'] for c in sa.inspect(bind).get_columns('whatsapp_conversations')}:
        op.add_column('whatsapp_conversations',sa.Column('ad_flow_opted_out',sa.Boolean(),nullable=False,server_default=sa.false()))


def downgrade():
    pass  # Retain mappings, opt-outs and execution history for a safe application rollback.
