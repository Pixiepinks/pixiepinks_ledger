"""Administrator ad-to-staff rules and automatic assignment evidence."""
from alembic import op
import sqlalchemy as sa
revision='20261109_37'
down_revision='20261109_36'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind();meta=sa.MetaData()
    sa.Table('users',meta,autoload_with=bind)
    rule=sa.Table('ad_assignment_rules',meta,
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('ad_id',sa.String(64),nullable=False),
        sa.Column('label',sa.String(120),nullable=False),sa.Column('staff_user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('enabled',sa.Boolean(),nullable=False),sa.Column('version',sa.Integer(),nullable=False),
        sa.Column('created_by_user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('updated_by_user_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('created_at',sa.DateTime(),nullable=False),sa.Column('updated_at',sa.DateTime(),nullable=False),
        sa.UniqueConstraint('ad_id',name='uq_ad_assignment_rules_ad_id'))
    rule.create(bind,checkfirst=True)
    sa.Index('ix_ad_assignment_rules_ad_id',rule.c.ad_id).create(bind,checkfirst=True)
    audit=sa.Table('ad_assignment_rule_audits',meta,
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('rule_id',sa.Integer(),sa.ForeignKey('ad_assignment_rules.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('version',sa.Integer(),nullable=False),sa.Column('changes',sa.JSON(),nullable=False),sa.Column('created_at',sa.DateTime(),nullable=False),
        sa.UniqueConstraint('rule_id','version',name='uq_ad_assignment_rule_audit_version'))
    audit.create(bind,checkfirst=True)
    sa.Index('ix_ad_assignment_rule_audits_rule_id',audit.c.rule_id).create(bind,checkfirst=True)
    columns={c['name'] for c in sa.inspect(bind).get_columns('conversation_assignment_audits')}
    for column in [sa.Column('source',sa.String(24),nullable=False,server_default='MANUAL'),
                   sa.Column('ad_id',sa.String(64)),sa.Column('rule_id',sa.Integer()),sa.Column('rule_version',sa.Integer())]:
        if column.name not in columns:op.add_column('conversation_assignment_audits',column)
    existing=sa.Table('conversation_assignment_audits',meta,autoload_with=bind)
    sa.Index('ix_conversation_assignment_audits_rule_id',existing.c.rule_id).create(bind,checkfirst=True)


def downgrade():
    pass  # Preserve rules and assignment history on application rollback.
