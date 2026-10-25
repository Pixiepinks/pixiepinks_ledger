"""Add safe run diagnostics and optional explicitly narrowed message consent scope."""
from alembic import op
import sqlalchemy as sa

revision='20261025_15'
down_revision='20261025_14'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind()
    if 'diagnostics' not in {c['name'] for c in sa.inspect(bind).get_columns('learning_jobs')}:
        op.add_column('learning_jobs',sa.Column('diagnostics',sa.JSON(),nullable=False,server_default=sa.text("'{}'")))
    if 'messages_from' not in {c['name'] for c in sa.inspect(bind).get_columns('learning_sources')}:
        op.add_column('learning_sources',sa.Column('messages_from',sa.DateTime(),nullable=True))


def downgrade():
    # Removing a consent bound could expand eligibility on the previous release.
    raise RuntimeError('Retain scope restrictions and diagnostics; roll back application behavior with this migration retained')
