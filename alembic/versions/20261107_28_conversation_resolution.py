"""Add retained conversation workload overrides and append-only audit history."""
from alembic import op
import sqlalchemy as sa
revision='20261107_28'
down_revision='20261107_27'
branch_labels=None
depends_on=None


def tables():
    md=sa.MetaData()
    # Referenced table stubs are for metadata FK resolution, not creation.
    sa.Table('whatsapp_conversations',md,sa.Column('id',sa.Integer(),primary_key=True))
    state=sa.Table('conversation_work_states',md,
        sa.Column('conversation_id',sa.Integer(),sa.ForeignKey('whatsapp_conversations.id',ondelete='RESTRICT'),primary_key=True),
        sa.Column('status',sa.String(24),nullable=False,server_default='NEEDS_REPLY',index=True),
        sa.Column('version',sa.Integer(),nullable=False,server_default='0'),sa.Column('updated_at',sa.DateTime(),nullable=False),
        sa.Column('updated_by',sa.Integer()),sa.Column('reason',sa.String(1000)),sa.Column('resolved_at',sa.DateTime()),
        sa.Column('due_at',sa.DateTime(),index=True),sa.Column('followup_user_id',sa.Integer(),index=True),
        sa.Column('inbound_id',sa.Integer()),sa.Column('inbound_at',sa.DateTime()),
        sa.Column('force_reply',sa.Boolean(),nullable=False,server_default=sa.false()),sa.Column('baseline_id',sa.Integer(),nullable=False,server_default='0'),sa.Column('review_cutoff_at',sa.DateTime()),sa.Column('ack_inbound_id',sa.Integer(),nullable=False,server_default='0'),
        sa.CheckConstraint("status IN ('NEEDS_REPLY','FOLLOW_UP_LATER','RESOLVED')",name='ck_conversation_work_status'))
    event=sa.Table('conversation_work_events',md,sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('conversation_id',sa.Integer(),sa.ForeignKey('whatsapp_conversations.id',ondelete='RESTRICT'),nullable=False,index=True),
        sa.Column('dedup_key',sa.String(160),nullable=False,unique=True),sa.Column('fingerprint',sa.String(64),nullable=False),
        sa.Column('previous_status',sa.String(24),nullable=False),sa.Column('new_status',sa.String(24),nullable=False),
        sa.Column('actor_id',sa.Integer()),sa.Column('actor_name',sa.String(100)),sa.Column('trigger',sa.String(16),nullable=False),
        sa.Column('reason',sa.String(1000)),sa.Column('inbound_id',sa.Integer()),sa.Column('due_at',sa.DateTime()),
        sa.Column('followup_user_id',sa.Integer()),sa.Column('created_at',sa.DateTime(),nullable=False,index=True))
    return state,event


def upgrade():
    bind=op.get_bind();state,event=tables()
    for table in [state,event]:
        table.create(bind,checkfirst=True)
        for index in table.indexes:index.create(bind,checkfirst=True)
    if 'ix_wam_work_turn' not in {i['name'] for i in sa.inspect(bind).get_indexes('whatsapp_conversation_messages')}:
        op.create_index('ix_wam_work_turn','whatsapp_conversation_messages',['phone_number','direction','created_at','id'])
    # NEEDS_REPLY is the neutral derived mode: force_reply=false leaves the existing
    # unanswered-turn calculation authoritative. Waiting histories are NOT resolved.
    bind.execute(sa.text("""INSERT INTO conversation_work_states
      (conversation_id,status,version,updated_at,force_reply,baseline_id,inbound_id,inbound_at)
      SELECT c.id,'NEEDS_REPLY',0,COALESCE(c.created_at,CURRENT_TIMESTAMP),false,0,
        (SELECT m.id FROM whatsapp_conversation_messages m WHERE m.phone_number=c.phone_number AND m.direction='inbound' ORDER BY m.created_at DESC,m.id DESC LIMIT 1),
        (SELECT MAX(m.created_at) FROM whatsapp_conversation_messages m WHERE m.phone_number=c.phone_number AND m.direction='inbound')
      FROM whatsapp_conversations c WHERE NOT EXISTS (SELECT 1 FROM conversation_work_states s WHERE s.conversation_id=c.id)"""))


def downgrade():
    # Retain status and audit evidence. A pre-feature app ignores these tables.
    pass
