"""Conversation owners, assignment version and administrator audit."""
from alembic import op
import sqlalchemy as sa
revision='20261103_23'
down_revision='20261103_22'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind();columns={c['name'] for c in sa.inspect(bind).get_columns('whatsapp_conversations')}
    additions=[sa.Column('assigned_to_user_id',sa.Integer(),sa.ForeignKey('users.id',name='fk_whatsapp_assigned_user'),nullable=True),
        sa.Column('assigned_by_user_id',sa.Integer(),sa.ForeignKey('users.id',name='fk_whatsapp_assigning_user'),nullable=True),
        sa.Column('assigned_at',sa.DateTime(),nullable=True),sa.Column('assignment_version',sa.Integer(),nullable=False,server_default='0')]
    if any(c.name not in columns for c in additions):
        with op.batch_alter_table('whatsapp_conversations') as batch:
            for column in additions:
                if column.name not in columns:batch.add_column(column)
    metadata=sa.MetaData()
    users=sa.Table('users',metadata,autoload_with=bind)
    conversations=sa.Table('whatsapp_conversations',metadata,autoload_with=bind)
    sa.Index('ix_whatsapp_conversations_assigned_to_user_id',conversations.c.assigned_to_user_id).create(bind,checkfirst=True)
    table=sa.Table('conversation_assignment_audits',metadata,
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('conversation_id',sa.Integer(),sa.ForeignKey('whatsapp_conversations.id'),nullable=False),
        sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('previous_owner_id',sa.Integer(),sa.ForeignKey('users.id')),sa.Column('new_owner_id',sa.Integer(),sa.ForeignKey('users.id')),
        sa.Column('created_at',sa.DateTime(),nullable=False))
    table.create(bind,checkfirst=True)
    sa.Index('ix_conversation_assignment_audits_conversation_id',table.c.conversation_id).create(bind,checkfirst=True)


def downgrade():
    op.drop_table('conversation_assignment_audits')
    op.drop_index('ix_whatsapp_conversations_assigned_to_user_id',table_name='whatsapp_conversations')
    with op.batch_alter_table('whatsapp_conversations') as batch:
        for name in ('fk_whatsapp_assigned_user','fk_whatsapp_assigning_user'):batch.drop_constraint(name,type_='foreignkey')
        for name in ('assignment_version','assigned_at','assigned_by_user_id','assigned_to_user_id'):batch.drop_column(name)
