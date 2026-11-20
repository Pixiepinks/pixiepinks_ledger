"""Add encrypted receipt connector replay fence; preserve hardware jobs/audits."""
from alembic import op
revision='20261121_49'
down_revision='20261120_48'
branch_labels=None
depends_on=None

def upgrade():
    from hardware_models import PrintConnectorNonce
    PrintConnectorNonce.__table__.create(op.get_bind(),checkfirst=True)

def downgrade():pass  # Retain replay evidence; code rollback must not drop live tables.
