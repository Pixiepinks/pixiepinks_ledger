"""Durable, opt-in customer questions; existing customers are never enrolled."""
from alembic import op
import sqlalchemy as sa
import json
revision='20261108_29'
down_revision='20261107_28'
branch_labels=None
depends_on=None


def upgrade():
    # Standalone schema definition, independent of future application models.
    md=sa.MetaData()
    sa.Table('whatsapp_conversations',md,sa.Column('id',sa.Integer(),primary_key=True))
    cfg=sa.Table('customer_question_config',md,sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('enabled',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('version',sa.Integer(),nullable=False,server_default='0'),sa.Column('definition',sa.JSON(),nullable=False))
    run=sa.Table('customer_question_runs',md,sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('conversation_id',sa.Integer(),sa.ForeignKey('whatsapp_conversations.id',ondelete='RESTRICT'),nullable=False,index=True),
        sa.Column('definition',sa.JSON(),nullable=False),sa.Column('status',sa.String(20),nullable=False),
        sa.Column('step',sa.Integer(),nullable=False),sa.Column('answers',sa.JSON(),nullable=False),
        sa.Column('token',sa.String(64),nullable=False,unique=True),sa.Column('provider_id',sa.String(255)),
        sa.Column('updated_at',sa.DateTime(),nullable=False),sa.Column('created_by',sa.String(100),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),
        sa.CheckConstraint("status IN ('QUEUED','SENDING','WAITING','PAUSED','STOPPED','COMPLETE','UNCERTAIN','WINDOW_CLOSED')",name='ck_customer_question_status'))
    event=sa.Table('customer_question_events',md,sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('run_id',sa.Integer(),sa.ForeignKey('customer_question_runs.id',ondelete='RESTRICT'),index=True),
        sa.Column('action',sa.String(40),nullable=False),sa.Column('actor',sa.String(100),nullable=False),
        sa.Column('detail',sa.JSON(),nullable=False),sa.Column('created_at',sa.DateTime(),nullable=False))
    bind=op.get_bind()
    for table in [cfg,run,event]:
        table.create(bind,checkfirst=True)
        for index in table.indexes:index.create(bind,checkfirst=True)
    if not bind.execute(sa.select(cfg.c.id).where(cfg.c.id==1)).first():
        bind.execute(cfg.insert().values(id=1,enabled=False,version=0,definition={'title':'Child preferences','steps':[
            {'key':'gender','body':'Who are you shopping for?','options':['Girl','Boy','Any']},
            {'key':'age','body':'What is the child’s age range?','options':['Under 3 years','3–6 years','7+ years']}]}))


def downgrade():
    # Retain answers and audit evidence. Older applications ignore these tables.
    pass
