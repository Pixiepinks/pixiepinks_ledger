"""Staff section grants and session revocation; retain existing user access."""
from alembic import op
import sqlalchemy as sa
revision='20261103_22'
down_revision='20261030_21'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind();columns={c['name'] for c in sa.inspect(bind).get_columns('users')}
    for column in [sa.Column('section_access',sa.JSON(),nullable=True),
                   sa.Column('is_active',sa.Boolean(),nullable=False,server_default=sa.true()),
                   sa.Column('session_version',sa.Integer(),nullable=False,server_default='0')]:
        if column.name not in columns:op.add_column('users',column)
    metadata=sa.MetaData();sa.Table('users',metadata,autoload_with=bind)
    sa.Table('staff_access_audits',metadata,
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('target_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('action',sa.String(40),nullable=False),sa.Column('changes',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False)).create(bind,checkfirst=True)


def downgrade():
    op.drop_table('staff_access_audits')
    with op.batch_alter_table('users') as batch:
        for name in ('session_version','is_active','section_access'):batch.drop_column(name)
