"""Optional unique WhatsApp conversation link; preserve all existing lead records."""
from alembic import op
import sqlalchemy as sa

revision='20261026_16'
down_revision='20261025_15'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind()
    if 'whatsapp_conversation_id' not in {c['name'] for c in sa.inspect(bind).get_columns('leads')}:
        if bind.dialect.name=='sqlite':
            # Native nullable ADD preserves referencing notes/tasks with FK checks on.
            bind.execute(sa.text('ALTER TABLE leads ADD COLUMN whatsapp_conversation_id INTEGER REFERENCES whatsapp_conversations(id) ON DELETE RESTRICT'))
        else:
            op.add_column('leads',sa.Column('whatsapp_conversation_id',sa.Integer(),sa.ForeignKey('whatsapp_conversations.id',name='fk_leads_whatsapp_conversation',ondelete='RESTRICT'),nullable=True))
    if 'ix_leads_whatsapp_conversation_id' not in {i['name'] for i in sa.inspect(bind).get_indexes('leads')}:
        op.create_index('ix_leads_whatsapp_conversation_id','leads',['whatsapp_conversation_id'],unique=True)


def downgrade():
    if op.get_bind().dialect.name=='sqlite':
        raise RuntimeError('Retain the additive lead link on SQLite when rolling back application behavior; rebuilding the parent could invalidate note/task foreign keys')
    op.drop_index('ix_leads_whatsapp_conversation_id',table_name='leads')
    op.drop_column('leads','whatsapp_conversation_id')
