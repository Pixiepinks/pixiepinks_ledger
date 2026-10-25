"""Add disabled-by-default learning jobs, evidence, review and knowledge version metadata."""
from datetime import datetime
from alembic import op
import sqlalchemy as sa

revision='20261025_14'
down_revision='20261025_13'
branch_labels=None
depends_on=None

DEFAULTS={'enabled': False, 'privacy_reviewed': False, 'notice_configured': False, 'provider_reviewed': False, 'publication_enabled': False, 'auto_publish': False, 'auto_paused': False, 'schedule': '02:00', 'timezone': 'Asia/Colombo', 'min_evidence': 3, 'confidence': 0.95, 'max_conversations': 10, 'max_tokens': 100000, 'daily_tokens': 250000, 'max_cost': 1.0, 'daily_cost': 2.0, 'usd_per_million_tokens': 10.0, 'model': 'gpt-4.1-mini', 'max_auto_per_day': 1, 'auto_allowlist': ['greeting.en', 'greeting.si', 'clarification.en', 'clarification.si'], 'evidence_days': 30, 'candidate_days': 180, 'evaluation_days': 90, 'job_log_days': 30, 'audit_days': 365, 'legal_hold': False, 'notify_in_app': True, 'quiet_hours': 24, 'catchup_days': 7}

def upgrade():
    bind=op.get_bind()
    if not sa.inspect(bind).has_table('learning_jobs'):
        op.create_table('learning_jobs',
            sa.Column('id',sa.String(length=36),primary_key=True,nullable=False),
            sa.Column('request_key',sa.String(length=128),nullable=False),
            sa.Column('job_type',sa.String(length=20),nullable=False),
            sa.Column('status',sa.String(length=24),nullable=False),
            sa.Column('checkpoint',sa.Integer(),nullable=False),
            sa.Column('conversations_processed',sa.Integer(),nullable=False),
            sa.Column('candidates_generated',sa.Integer(),nullable=False),
            sa.Column('candidates_rejected',sa.Integer(),nullable=False),
            sa.Column('candidates_auto_published',sa.Integer(),nullable=False),
            sa.Column('candidates_pending_review',sa.Integer(),nullable=False),
            sa.Column('token_usage',sa.Integer(),nullable=False),
            sa.Column('reserved_tokens',sa.Integer(),nullable=False),
            sa.Column('estimated_cost',sa.Numeric(precision=14, scale=6),nullable=False),
            sa.Column('error_count',sa.Integer(),nullable=False),
            sa.Column('error_summary',sa.String(length=80),nullable=True),
            sa.Column('attempts',sa.Integer(),nullable=False),
            sa.Column('next_retry_at',sa.DateTime(),nullable=True),
            sa.Column('created_at',sa.DateTime(),nullable=False),
            sa.Column('started_at',sa.DateTime(),nullable=True),
            sa.Column('completed_at',sa.DateTime(),nullable=True),
            sa.UniqueConstraint('request_key',name=None),
        )
        op.create_index('ix_learning_jobs_status','learning_jobs',['status'],unique=False)
        op.create_index('ix_learning_jobs_created_at','learning_jobs',['created_at'],unique=False)
    if not sa.inspect(bind).has_table('learning_leases'):
        op.create_table('learning_leases',
            sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
            sa.Column('owner',sa.String(length=36),nullable=True),
            sa.Column('expires_at',sa.DateTime(),nullable=False),
            sa.Column('last_seen_at',sa.DateTime(),nullable=True),
        )
    if not sa.inspect(bind).has_table('learning_actions'):
        op.create_table('learning_actions',
            sa.Column('request_key',sa.String(length=100),primary_key=True,nullable=False),
            sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('fingerprint',sa.String(length=64),nullable=False),
            sa.Column('result',sa.JSON(),nullable=False),
            sa.Column('created_at',sa.DateTime(),nullable=False),
        )
    if not sa.inspect(bind).has_table('learning_audit_logs'):
        op.create_table('learning_audit_logs',
            sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
            sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL'),nullable=True),
            sa.Column('action',sa.String(length=50),nullable=False),
            sa.Column('entity_type',sa.String(length=30),nullable=False),
            sa.Column('entity_id',sa.String(length=80),nullable=True),
            sa.Column('previous_state',sa.JSON(),nullable=False),
            sa.Column('new_state',sa.JSON(),nullable=False),
            sa.Column('timestamp',sa.DateTime(),nullable=False),
        )
        op.create_index('ix_learning_audit_logs_timestamp','learning_audit_logs',['timestamp'],unique=False)
    if not sa.inspect(bind).has_table('learning_candidates'):
        op.create_table('learning_candidates',
            sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
            sa.Column('category',sa.String(length=24),nullable=False),
            sa.Column('title',sa.String(length=120),nullable=False),
            sa.Column('proposed_content',sa.Text(),nullable=False),
            sa.Column('language',sa.String(length=8),nullable=False),
            sa.Column('risk_level',sa.String(length=24),nullable=False),
            sa.Column('confidence_score',sa.Numeric(precision=5, scale=4),nullable=False),
            sa.Column('evidence_count',sa.Integer(),nullable=False),
            sa.Column('validation_status',sa.String(length=24),nullable=False),
            sa.Column('publication_status',sa.String(length=24),nullable=False),
            sa.Column('review_required',sa.Boolean(),nullable=False),
            sa.Column('reviewer_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL'),nullable=True),
            sa.Column('reviewed_at',sa.DateTime(),nullable=True),
            sa.Column('rejection_reason',sa.String(length=300),nullable=True),
            sa.Column('analysis_summary',sa.JSON(),nullable=False),
            sa.Column('content_hash',sa.String(length=64),nullable=False),
            sa.Column('product_handle',sa.String(length=255),nullable=True),
            sa.Column('product_category',sa.String(length=100),nullable=True),
            sa.Column('knowledge_entry_id',sa.Integer(),sa.ForeignKey('bot_knowledge_entries.id',ondelete='RESTRICT'),nullable=True),
            sa.Column('auto_published',sa.Boolean(),nullable=False),
            sa.Column('created_at',sa.DateTime(),nullable=False),
            sa.Column('updated_at',sa.DateTime(),nullable=False),
            sa.UniqueConstraint('content_hash',name=None),
        )
        op.create_index('ix_learning_candidates_created_at','learning_candidates',['created_at'],unique=False)
        op.create_index('ix_learning_candidates_publication_status','learning_candidates',['publication_status'],unique=False)
    if not sa.inspect(bind).has_table('learning_settings'):
        op.create_table('learning_settings',
            sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
            sa.Column('revision',sa.Integer(),nullable=False),
            sa.Column('config',sa.JSON(),nullable=False),
            sa.Column('updated_at',sa.DateTime(),nullable=False),
            sa.Column('updated_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL'),nullable=True),
        )
    if not sa.inspect(bind).has_table('learning_sources'):
        op.create_table('learning_sources',
            sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
            sa.Column('conversation_id',sa.Integer(),sa.ForeignKey('whatsapp_conversations.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('allowed',sa.Boolean(),nullable=False),
            sa.Column('evaluation_only',sa.Boolean(),nullable=False),
            sa.Column('consent_reference',sa.String(length=200),nullable=True),
            sa.Column('consent_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL'),nullable=True),
            sa.Column('consent_at',sa.DateTime(),nullable=True),
            sa.Column('withdrawn_at',sa.DateTime(),nullable=True),
            sa.Column('legal_hold',sa.Boolean(),nullable=False),
            sa.Column('last_message_id',sa.Integer(),nullable=False),
            sa.Column('analyzer_version',sa.String(length=40),nullable=False),
            sa.Column('updated_at',sa.DateTime(),nullable=False),
            sa.UniqueConstraint('conversation_id',name=None),
        )
    if not sa.inspect(bind).has_table('learning_analyses'):
        op.create_table('learning_analyses',
            sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
            sa.Column('source_id',sa.Integer(),sa.ForeignKey('learning_sources.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('job_id',sa.String(length=36),sa.ForeignKey('learning_jobs.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('end_message_id',sa.Integer(),nullable=False),
            sa.Column('completed_at',sa.DateTime(),nullable=True),
            sa.Column('analyzer_version',sa.String(length=40),nullable=False),
            sa.Column('status',sa.String(length=24),nullable=False),
            sa.Column('error_code',sa.String(length=80),nullable=True),
            sa.Column('created_at',sa.DateTime(),nullable=False),
            sa.UniqueConstraint('source_id','end_message_id','analyzer_version',name='uq_learning_analysis_window'),
        )
        op.create_index('ix_learning_analyses_source_id','learning_analyses',['source_id'],unique=False)
    if not sa.inspect(bind).has_table('learning_evaluations'):
        op.create_table('learning_evaluations',
            sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
            sa.Column('candidate_id',sa.Integer(),sa.ForeignKey('learning_candidates.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('evaluation_version',sa.String(length=40),nullable=False),
            sa.Column('content_hash',sa.String(length=64),nullable=False),
            sa.Column('test_case_count',sa.Integer(),nullable=False),
            sa.Column('passed_count',sa.Integer(),nullable=False),
            sa.Column('failed_count',sa.Integer(),nullable=False),
            sa.Column('safety_result',sa.Boolean(),nullable=False),
            sa.Column('factuality_result',sa.Boolean(),nullable=False),
            sa.Column('regression_result',sa.Boolean(),nullable=False),
            sa.Column('evaluation_details',sa.JSON(),nullable=False),
            sa.Column('created_at',sa.DateTime(),nullable=False),
        )
        op.create_index('ix_learning_evaluations_candidate_id','learning_evaluations',['candidate_id'],unique=False)
    if not sa.inspect(bind).has_table('learning_evidence'):
        op.create_table('learning_evidence',
            sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
            sa.Column('candidate_id',sa.Integer(),sa.ForeignKey('learning_candidates.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('source_id',sa.Integer(),sa.ForeignKey('learning_sources.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('analysis_id',sa.Integer(),sa.ForeignKey('learning_analyses.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('source_message_references',sa.JSON(),nullable=False),
            sa.Column('sanitized_excerpt',sa.JSON(),nullable=False),
            sa.Column('evidence_type',sa.String(length=32),nullable=False),
            sa.Column('outcome_signal',sa.String(length=32),nullable=False),
            sa.Column('created_at',sa.DateTime(),nullable=False),
            sa.UniqueConstraint('candidate_id','analysis_id',name='uq_learning_evidence_analysis'),
        )
        op.create_index('ix_learning_evidence_candidate_id','learning_evidence',['candidate_id'],unique=False)
        op.create_index('ix_learning_evidence_created_at','learning_evidence',['created_at'],unique=False)
        op.create_index('ix_learning_evidence_source_id','learning_evidence',['source_id'],unique=False)
    if not sa.inspect(bind).has_table('learning_knowledge_versions'):
        op.create_table('learning_knowledge_versions',
            sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
            sa.Column('knowledge_item_id',sa.Integer(),sa.ForeignKey('bot_knowledge_entries.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('candidate_id',sa.Integer(),sa.ForeignKey('learning_candidates.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('version_number',sa.Integer(),nullable=False),
            sa.Column('content',sa.Text(),nullable=False),
            sa.Column('content_hash',sa.String(length=64),nullable=False),
            sa.Column('source_type',sa.String(length=16),nullable=False),
            sa.Column('publication_status',sa.String(length=24),nullable=False),
            sa.Column('published_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL'),nullable=True),
            sa.Column('evaluation_id',sa.Integer(),sa.ForeignKey('learning_evaluations.id',ondelete='RESTRICT'),nullable=True),
            sa.Column('rollback_reference',sa.Integer(),sa.ForeignKey('learning_knowledge_versions.id',ondelete='RESTRICT'),nullable=True),
            sa.Column('created_at',sa.DateTime(),nullable=False),
            sa.UniqueConstraint('knowledge_item_id','version_number',name='uq_learning_knowledge_version'),
        )
        op.create_index('ix_learning_knowledge_versions_created_at','learning_knowledge_versions',['created_at'],unique=False)
        op.create_index('ix_learning_knowledge_versions_knowledge_item_id','learning_knowledge_versions',['knowledge_item_id'],unique=False)
    if not sa.inspect(bind).has_table('learning_feedback'):
        op.create_table('learning_feedback',
            sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
            sa.Column('kind',sa.String(length=32),nullable=False),
            sa.Column('version_id',sa.Integer(),sa.ForeignKey('learning_knowledge_versions.id',ondelete='RESTRICT'),nullable=True),
            sa.Column('conversation_id',sa.Integer(),sa.ForeignKey('whatsapp_conversations.id',ondelete='RESTRICT'),nullable=True),
            sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL'),nullable=True),
            sa.Column('created_at',sa.DateTime(),nullable=False),
        )
        op.create_index('ix_learning_feedback_created_at','learning_feedback',['created_at'],unique=False)
        op.create_index('ix_learning_feedback_kind','learning_feedback',['kind'],unique=False)
    if not sa.inspect(bind).has_table('learning_reservations'):
        op.create_table('learning_reservations',
            sa.Column('id',sa.Integer(),primary_key=True),
            sa.Column('job_id',sa.String(36),sa.ForeignKey('learning_jobs.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('tokens',sa.Integer(),nullable=False),sa.Column('cost',sa.Numeric(14,6),nullable=False),
            sa.Column('created_at',sa.DateTime(),nullable=False))
        op.create_index('ix_learning_reservations_job_id','learning_reservations',['job_id'])
        op.create_index('ix_learning_reservations_created_at','learning_reservations',['created_at'])
    if not sa.inspect(bind).has_table('learning_regression_cases'):
        op.create_table('learning_regression_cases',
            sa.Column('id',sa.Integer(),primary_key=True),
            sa.Column('source_id',sa.Integer(),sa.ForeignKey('learning_sources.id',ondelete='RESTRICT'),nullable=False),
            sa.Column('source_message_references',sa.JSON(),nullable=False),
            sa.Column('sanitized_question',sa.Text(),nullable=False),
            sa.Column('expected_behavior',sa.String(32),nullable=False),sa.Column('language',sa.String(8),nullable=False),
            sa.Column('case_hash',sa.String(64),nullable=False,unique=True),
            sa.Column('approved_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL')),
            sa.Column('active',sa.Boolean(),nullable=False),sa.Column('created_at',sa.DateTime(),nullable=False))
        op.create_index('ix_learning_regression_cases_source_id','learning_regression_cases',['source_id'])
    settings=sa.table('learning_settings',sa.column('id'),sa.column('revision'),sa.column('config',sa.JSON()),sa.column('updated_at'))
    if not bind.execute(sa.select(settings.c.id).where(settings.c.id==1)).first():
        bind.execute(settings.insert().values(id=1,revision=0,config=DEFAULTS,updated_at=datetime.utcnow()))
    lease=sa.table('learning_leases',sa.column('id'),sa.column('expires_at'))
    if not bind.execute(sa.select(lease.c.id).where(lease.c.id==1)).first():
        bind.execute(lease.insert().values(id=1,expires_at=datetime(1970,1,1)))

def downgrade():
    raise RuntimeError('Preserve evidence, privacy exclusions and publication history; retain this revision when rolling back application behavior')
