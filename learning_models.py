"""Learning-only persistence. Raw WhatsApp history and live bot settings are untouched."""
from datetime import datetime
from sqlalchemy import Column,Integer,String,Text,DateTime,Boolean,JSON,Numeric,ForeignKey,UniqueConstraint
from database import Base


class LearningSettings(Base):
    __tablename__='learning_settings'
    id=Column(Integer,primary_key=True)
    revision=Column(Integer,nullable=False,default=0)
    config=Column(JSON,nullable=False)
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    updated_by=Column(Integer,ForeignKey('users.id',ondelete='SET NULL'))


class LearningLease(Base):
    __tablename__='learning_leases'
    id=Column(Integer,primary_key=True)
    owner=Column(String(36))
    expires_at=Column(DateTime,nullable=False,default=datetime.utcnow)
    last_seen_at=Column(DateTime)


class LearningSource(Base):
    __tablename__='learning_sources'
    id=Column(Integer,primary_key=True)
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id',ondelete='RESTRICT'),unique=True,nullable=False)
    allowed=Column(Boolean,nullable=False,default=False)
    evaluation_only=Column(Boolean,nullable=False,default=False)
    consent_reference=Column(String(200))
    consent_by=Column(Integer,ForeignKey('users.id',ondelete='SET NULL'))
    consent_at=Column(DateTime)
    withdrawn_at=Column(DateTime)
    legal_hold=Column(Boolean,nullable=False,default=False)
    last_message_id=Column(Integer,nullable=False,default=0)
    analyzer_version=Column(String(40),nullable=False,default='1')
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class LearningJob(Base):
    __tablename__='learning_jobs'
    id=Column(String(36),primary_key=True)
    request_key=Column(String(128),unique=True,nullable=False)
    job_type=Column(String(20),nullable=False)
    status=Column(String(24),nullable=False,index=True,default='QUEUED')
    checkpoint=Column(Integer,nullable=False,default=0)
    conversations_processed=Column(Integer,nullable=False,default=0)
    candidates_generated=Column(Integer,nullable=False,default=0)
    candidates_rejected=Column(Integer,nullable=False,default=0)
    candidates_auto_published=Column(Integer,nullable=False,default=0)
    candidates_pending_review=Column(Integer,nullable=False,default=0)
    token_usage=Column(Integer,nullable=False,default=0)
    reserved_tokens=Column(Integer,nullable=False,default=0)
    estimated_cost=Column(Numeric(14,6),nullable=False,default=0)
    error_count=Column(Integer,nullable=False,default=0)
    error_summary=Column(String(80))
    attempts=Column(Integer,nullable=False,default=0)
    next_retry_at=Column(DateTime)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)
    started_at=Column(DateTime)
    completed_at=Column(DateTime)


class LearningAnalysis(Base):
    __tablename__='learning_analyses'
    __table_args__=(UniqueConstraint('source_id','end_message_id','analyzer_version',name='uq_learning_analysis_window'),)
    id=Column(Integer,primary_key=True)
    source_id=Column(Integer,ForeignKey('learning_sources.id',ondelete='RESTRICT'),nullable=False,index=True)
    job_id=Column(String(36),ForeignKey('learning_jobs.id',ondelete='RESTRICT'),nullable=False)
    end_message_id=Column(Integer,nullable=False)
    analyzer_version=Column(String(40),nullable=False)
    status=Column(String(24),nullable=False)
    error_code=Column(String(80))
    completed_at=Column(DateTime)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class LearningCandidate(Base):
    __tablename__='learning_candidates'
    id=Column(Integer,primary_key=True)
    category=Column(String(24),nullable=False)
    title=Column(String(120),nullable=False)
    proposed_content=Column(Text,nullable=False)
    language=Column(String(8),nullable=False)
    risk_level=Column(String(24),nullable=False)
    confidence_score=Column(Numeric(5,4),nullable=False)
    evidence_count=Column(Integer,nullable=False,default=0)
    validation_status=Column(String(24),nullable=False,default='PENDING')
    publication_status=Column(String(24),nullable=False,default='DRAFT',index=True)
    review_required=Column(Boolean,nullable=False,default=True)
    reviewer_id=Column(Integer,ForeignKey('users.id',ondelete='SET NULL'))
    reviewed_at=Column(DateTime)
    rejection_reason=Column(String(300))
    analysis_summary=Column(JSON,nullable=False,default=dict)
    content_hash=Column(String(64),unique=True,nullable=False)
    product_handle=Column(String(255))
    product_category=Column(String(100))
    knowledge_entry_id=Column(Integer,ForeignKey('bot_knowledge_entries.id',ondelete='RESTRICT'))
    auto_published=Column(Boolean,nullable=False,default=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)
    updated_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class LearningEvidence(Base):
    __tablename__='learning_evidence'
    __table_args__=(UniqueConstraint('candidate_id','analysis_id',name='uq_learning_evidence_analysis'),)
    id=Column(Integer,primary_key=True)
    candidate_id=Column(Integer,ForeignKey('learning_candidates.id',ondelete='RESTRICT'),nullable=False,index=True)
    source_id=Column(Integer,ForeignKey('learning_sources.id',ondelete='RESTRICT'),nullable=False,index=True)
    analysis_id=Column(Integer,ForeignKey('learning_analyses.id',ondelete='RESTRICT'),nullable=False)
    source_message_references=Column(JSON,nullable=False)
    sanitized_excerpt=Column(JSON,nullable=False)
    evidence_type=Column(String(32),nullable=False)
    outcome_signal=Column(String(32),nullable=False,default='UNKNOWN')
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)


class LearningEvaluation(Base):
    __tablename__='learning_evaluations'
    id=Column(Integer,primary_key=True)
    candidate_id=Column(Integer,ForeignKey('learning_candidates.id',ondelete='RESTRICT'),nullable=False,index=True)
    evaluation_version=Column(String(40),nullable=False)
    content_hash=Column(String(64),nullable=False)
    test_case_count=Column(Integer,nullable=False)
    passed_count=Column(Integer,nullable=False)
    failed_count=Column(Integer,nullable=False)
    safety_result=Column(Boolean,nullable=False)
    factuality_result=Column(Boolean,nullable=False)
    regression_result=Column(Boolean,nullable=False)
    evaluation_details=Column(JSON,nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class LearningKnowledgeVersion(Base):
    __tablename__='learning_knowledge_versions'
    __table_args__=(UniqueConstraint('knowledge_item_id','version_number',name='uq_learning_knowledge_version'),)
    id=Column(Integer,primary_key=True)
    knowledge_item_id=Column(Integer,ForeignKey('bot_knowledge_entries.id',ondelete='RESTRICT'),nullable=False,index=True)
    candidate_id=Column(Integer,ForeignKey('learning_candidates.id',ondelete='RESTRICT'),nullable=False)
    version_number=Column(Integer,nullable=False)
    content=Column(Text,nullable=False)
    content_hash=Column(String(64),nullable=False)
    source_type=Column(String(16),nullable=False)
    publication_status=Column(String(24),nullable=False)
    published_by=Column(Integer,ForeignKey('users.id',ondelete='SET NULL'))
    evaluation_id=Column(Integer,ForeignKey('learning_evaluations.id',ondelete='RESTRICT'))
    rollback_reference=Column(Integer,ForeignKey('learning_knowledge_versions.id',ondelete='RESTRICT'))
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)


class LearningAudit(Base):
    __tablename__='learning_audit_logs'
    id=Column(Integer,primary_key=True)
    actor_id=Column(Integer,ForeignKey('users.id',ondelete='SET NULL'))
    action=Column(String(50),nullable=False)
    entity_type=Column(String(30),nullable=False)
    entity_id=Column(String(80))
    previous_state=Column(JSON,nullable=False,default=dict)
    new_state=Column(JSON,nullable=False,default=dict)
    timestamp=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)


class LearningAction(Base):
    __tablename__='learning_actions'
    request_key=Column(String(100),primary_key=True)
    actor_id=Column(Integer,ForeignKey('users.id',ondelete='RESTRICT'),nullable=False)
    fingerprint=Column(String(64),nullable=False)
    result=Column(JSON,nullable=False,default=dict)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class LearningFeedback(Base):
    __tablename__='learning_feedback'
    id=Column(Integer,primary_key=True)
    kind=Column(String(32),nullable=False,index=True)
    version_id=Column(Integer,ForeignKey('learning_knowledge_versions.id',ondelete='RESTRICT'))
    conversation_id=Column(Integer,ForeignKey('whatsapp_conversations.id',ondelete='RESTRICT'))
    actor_id=Column(Integer,ForeignKey('users.id',ondelete='SET NULL'))
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)


class LearningReservation(Base):
    __tablename__='learning_reservations'
    id=Column(Integer,primary_key=True)
    job_id=Column(String(36),ForeignKey('learning_jobs.id',ondelete='RESTRICT'),nullable=False,index=True)
    tokens=Column(Integer,nullable=False)
    cost=Column(Numeric(14,6),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow,index=True)


class LearningRegressionCase(Base):
    __tablename__='learning_regression_cases'
    id=Column(Integer,primary_key=True)
    source_id=Column(Integer,ForeignKey('learning_sources.id',ondelete='RESTRICT'),nullable=False,index=True)
    source_message_references=Column(JSON,nullable=False)
    sanitized_question=Column(Text,nullable=False)
    expected_behavior=Column(String(32),nullable=False)
    language=Column(String(8),nullable=False)
    case_hash=Column(String(64),nullable=False,unique=True)
    approved_by=Column(Integer,ForeignKey('users.id',ondelete='SET NULL'))
    active=Column(Boolean,nullable=False,default=True)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)
