"""Preserve the original question flow and add a saved flow library."""
from alembic import op
import sqlalchemy as sa
from datetime import datetime
revision='20261108_30'
down_revision='20261108_29'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind();md=sa.MetaData()
    flow=sa.Table('customer_question_flows',md,sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('definition',sa.JSON(),nullable=False),sa.Column('archived',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('version',sa.Integer(),nullable=False,server_default='0'),sa.Column('created_by',sa.String(100),nullable=False),
        sa.Column('updated_at',sa.DateTime(),nullable=False))
    flow.create(bind,checkfirst=True)
    config=sa.Table('customer_question_config',md,autoload_with=bind)
    if not bind.execute(sa.select(flow.c.id).where(flow.c.id==1)).first():
        definition=bind.execute(sa.select(config.c.definition).where(config.c.id==1)).scalar_one()
        bind.execute(flow.insert().values(id=1,definition=definition,archived=False,version=0,created_by='Migration',updated_at=datetime.utcnow()))
        # Explicit IDs do not advance a PostgreSQL sequence.
        if bind.dialect.name=='postgresql':
            bind.execute(sa.text("SELECT setval(pg_get_serial_sequence('customer_question_flows','id'),(SELECT MAX(id) FROM customer_question_flows))"))
    if 'flow_id' not in {column['name'] for column in sa.inspect(bind).get_columns('customer_question_runs')}:
        op.add_column('customer_question_runs',sa.Column('flow_id',sa.Integer(),nullable=True))
    # All pre-library runs originated from the one original configuration.
    bind.execute(sa.text('UPDATE customer_question_runs SET flow_id=1 WHERE flow_id IS NULL'))


def downgrade():
    # Keep the library and run provenance; older applications ignore them.
    pass
