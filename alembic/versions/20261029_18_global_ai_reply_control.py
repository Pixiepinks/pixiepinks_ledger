"""Global automatic-reply switch and immutable inbound admission generation."""
from alembic import op
import sqlalchemy as sa

revision = "20261029_18"
down_revision = "20261027_17"
branch_labels = None
depends_on = None


def upgrade():
    from models import GlobalAIReplyControl
    bind = op.get_bind()
    GlobalAIReplyControl.__table__.create(bind, checkfirst=True)
    if not bind.execute(sa.select(GlobalAIReplyControl.id).where(GlobalAIReplyControl.id == 1)).first():
        bind.execute(GlobalAIReplyControl.__table__.insert().values(id=1, enabled=True, generation=0))
    columns = {c["name"] for c in sa.inspect(bind).get_columns("processed_whatsapp_messages")}
    if "ai_reply_generation" not in columns:
        op.add_column("processed_whatsapp_messages", sa.Column("ai_reply_generation", sa.Integer(), nullable=True))
    # Existing receipts intentionally stay NULL; do not resurrect old jobs.


def downgrade():
    op.drop_column("processed_whatsapp_messages", "ai_reply_generation")
    op.drop_table("global_ai_reply_control")
