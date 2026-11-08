"""Durable per-product send claims for deterministic question flows."""
from alembic import op
import sqlalchemy as sa
revision='20261108_31'
down_revision='20261108_30'
branch_labels=None
depends_on=None


def upgrade():
    md=sa.MetaData();sa.Table('customer_question_runs',md,sa.Column('id',sa.Integer(),primary_key=True))
    table=sa.Table('customer_question_product_sends',md,sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('run_id',sa.Integer(),sa.ForeignKey('customer_question_runs.id',ondelete='RESTRICT'),nullable=False,index=True),
        sa.Column('step',sa.Integer(),nullable=False),sa.Column('position',sa.Integer(),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),sa.Column('provider_id',sa.String(255)),sa.Column('reason',sa.String(200)),
        sa.Column('created_at',sa.DateTime(),nullable=False),
        sa.UniqueConstraint('run_id','step','position',name='uq_question_product_position'),
        sa.CheckConstraint("status IN ('QUEUED','SENDING','SENT','SKIPPED','FAILED','UNCERTAIN')",name='ck_question_product_status'))
    table.create(op.get_bind(),checkfirst=True)
    for index in table.indexes:index.create(op.get_bind(),checkfirst=True)


def downgrade():
    pass  # Preserve send evidence; older applications ignore the table.
