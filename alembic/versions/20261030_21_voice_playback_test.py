"""Add owner-controlled A/B diagnostic records; no audio or settings migration."""
from alembic import op
import sqlalchemy as sa
revision = '20261030_21'
down_revision = '20261030_20'
branch_labels = None
depends_on = None


def upgrade():
    # checkfirst supports installations created from Base.metadata before stamp.
    metadata=sa.MetaData()
    for table in ('users','saved_voices'):
        sa.Table(table,metadata,autoload_with=op.get_bind())
    run=sa.Table('voice_playback_tests',metadata,
        sa.Column('id',sa.String(32),primary_key=True),
        sa.Column('staff_id',sa.Integer,sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('voice_id',sa.Integer,sa.ForeignKey('saved_voices.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('voice_revision',sa.Integer,nullable=False),sa.Column('storage_name',sa.String(40),nullable=False),
        sa.Column('recipient_hash',sa.String(64),nullable=False),sa.Column('status',sa.String(24),nullable=False),
        sa.Column('evidence',sa.JSON,nullable=False),sa.Column('created_at',sa.DateTime,nullable=False),
        sa.Column('confirmed_at',sa.DateTime),sa.Column('results_at',sa.DateTime),
        sa.Column('results_by',sa.Integer),sa.Column('playback',sa.JSON))
    variant=sa.Table('voice_playback_variants',metadata,
        sa.Column('id',sa.Integer,primary_key=True),
        sa.Column('run_id',sa.String(32),sa.ForeignKey('voice_playback_tests.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('variant',sa.String(1),nullable=False),sa.Column('status',sa.String(24),nullable=False),
        sa.Column('audio_trace',sa.JSON),sa.Column('media_id',sa.String(255)),
        sa.Column('whatsapp_message_id',sa.String(255),index=True),sa.Column('delivery_status',sa.String(24)),
        sa.Column('delivery_error_code',sa.Integer),sa.UniqueConstraint('run_id','variant',name='uq_voice_playback_variant'))
    run.create(op.get_bind(),checkfirst=True)
    variant.create(op.get_bind(),checkfirst=True)


def downgrade():
    op.drop_table('voice_playback_variants')
    op.drop_table('voice_playback_tests')
