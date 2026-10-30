"""Voice validation state, retained originals and delivery diagnostics."""
from alembic import op
import sqlalchemy as sa

revision='20261030_19'
down_revision='20261029_18'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind()
    fields={
        'saved_voices':[
            sa.Column('original_storage_name',sa.String(40)),
            sa.Column('normalization_version',sa.Integer(),nullable=False,server_default='0'),
            sa.Column('processing_status',sa.String(24),nullable=False,server_default='PENDING'),
            sa.Column('normalization_error',sa.String(200)),
            sa.Column('normalization_token',sa.String(32)),
            sa.Column('normalization_started_at',sa.DateTime()),
            sa.Column('audio_diagnostics',sa.JSON())],
        'saved_voice_sends':[sa.Column('delivery_status',sa.String(24)),sa.Column('delivery_error_code',sa.Integer())]}
    for table,columns in fields.items():
        present={c['name'] for c in sa.inspect(bind).get_columns(table)}
        for column in columns:
            if column.name not in present:op.add_column(table,column)
    indexes={index['name'] for index in sa.inspect(bind).get_indexes('saved_voice_sends')}
    if 'ix_saved_voice_sends_whatsapp_message_id' not in indexes:
        op.create_index('ix_saved_voice_sends_whatsapp_message_id','saved_voice_sends',['whatsapp_message_id'])
    # Existing files and their metadata are deliberately retained, not trusted
    # or mass-transcoded during startup. Explicit prepare/first send validates.


def downgrade():
    op.drop_index('ix_saved_voice_sends_whatsapp_message_id',table_name='saved_voice_sends')
    for field in ['delivery_status','delivery_error_code']:op.drop_column('saved_voice_sends',field)
    for field in ['audio_diagnostics','normalization_started_at','normalization_token','normalization_error','processing_status','normalization_version','original_storage_name']:
        op.drop_column('saved_voices',field)
    # Never delete original or normalized audio files.
