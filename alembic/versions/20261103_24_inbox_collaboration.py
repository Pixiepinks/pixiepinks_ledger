"""Private conversation notes, explicit shares, mentions and share audit."""
from alembic import op
import sqlalchemy as sa
revision='20261103_24'
down_revision='20261103_23'
branch_labels=None
depends_on=None


def tables():
    metadata=sa.MetaData()
    sa.Table('users',metadata,sa.Column('id',sa.Integer(),primary_key=True))
    sa.Table('whatsapp_conversations',metadata,sa.Column('id',sa.Integer(),primary_key=True))
    def col(name,target):return sa.Column(name,sa.Integer(),sa.ForeignKey(target),nullable=False)
    shares=sa.Table('conversation_shares',metadata,sa.Column('id',sa.Integer(),primary_key=True),
        col('conversation_id','whatsapp_conversations.id'),col('user_id','users.id'),
        sa.Column('can_reply',sa.Boolean(),nullable=False),col('granted_by','users.id'),
        sa.Column('created_at',sa.DateTime(),nullable=False),sa.UniqueConstraint('conversation_id','user_id',name='uq_conversation_share_user'))
    audit=sa.Table('conversation_share_audits',metadata,sa.Column('id',sa.Integer(),primary_key=True),
        col('conversation_id','whatsapp_conversations.id'),col('actor_id','users.id'),col('user_id','users.id'),
        sa.Column('action',sa.String(20),nullable=False),sa.Column('can_reply',sa.Boolean(),nullable=False),sa.Column('created_at',sa.DateTime(),nullable=False))
    notes=sa.Table('conversation_notes',metadata,sa.Column('id',sa.Integer(),primary_key=True),
        col('conversation_id','whatsapp_conversations.id'),col('author_id','users.id'),sa.Column('client_key',sa.String(80),nullable=False),
        sa.Column('body',sa.Text(),nullable=False),sa.Column('created_at',sa.DateTime(),nullable=False),
        sa.UniqueConstraint('conversation_id','author_id','client_key',name='uq_conversation_note_key'))
    mentions=sa.Table('conversation_mentions',metadata,sa.Column('id',sa.Integer(),primary_key=True),
        col('note_id','conversation_notes.id'),col('user_id','users.id'),sa.Column('read_at',sa.DateTime()),
        sa.UniqueConstraint('note_id','user_id',name='uq_conversation_mention_user'))
    for table,cols in [(shares,['conversation_id','user_id']),(audit,['conversation_id']),(notes,['conversation_id']),(mentions,['note_id','user_id'])]:
        for column in cols:sa.Index('ix_'+table.name+'_'+column,table.c[column])
    return shares,audit,notes,mentions


def upgrade():
    bind=op.get_bind()
    for table in tables():
        table.create(bind,checkfirst=True)
        for index in table.indexes:index.create(bind,checkfirst=True)


def downgrade():
    for table in reversed(tables()):table.drop(op.get_bind(),checkfirst=True)
