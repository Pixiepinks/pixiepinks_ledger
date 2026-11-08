"""Retain exact numbered labels for customer product selection lists."""
from alembic import op
import sqlalchemy as sa
revision='20261108_32'
down_revision='20261108_31'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind()
    if 'choice_label' not in {c['name'] for c in sa.inspect(bind).get_columns('customer_question_product_sends')}:
        op.add_column('customer_question_product_sends',sa.Column('choice_label',sa.String(24),nullable=True))


def downgrade():
    pass  # Retain selection evidence; existing runs/flows are not rewritten.
