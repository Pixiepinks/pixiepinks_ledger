"""Transactional learning review, evaluation, publication, withdrawal and retention."""
from copy import deepcopy
from datetime import datetime,timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
import re

from sqlalchemy import func
from models import BotConfiguration,BotKnowledgeEntry,WhatsAppConversation
from learning_models import (LearningSettings,LearningSource,LearningJob,LearningCandidate,LearningEvidence,
    LearningEvaluation,LearningKnowledgeVersion,LearningAudit,LearningFeedback,LearningRegressionCase,LearningAnalysis)
from learning_policy import (DEFAULTS,COMMUNICATION,EVALUATION_VERSION,classify,prohibited,sanitize,
    content_hash,similar,CURRENCY,validate_settings)
from settings import settings


class LearningError(ValueError):
    pass


def config_row(db,lock=False):
    query=db.query(LearningSettings).filter_by(id=1)
    row=(query.populate_existing().with_for_update() if lock else query).one_or_none()
    if row is None:raise LearningError('Learning schema is not initialized')
    if lock:
        row.revision+=1;db.flush()  # Also serialize writers on SQLite.
    return row


def configuration(db):
    return {**DEFAULTS,**config_row(db).config}


def privacy_ready(config):
    return all(config[key] for key in ('privacy_reviewed','notice_configured','provider_reviewed'))


def learning_enabled(config):
    return settings.LEARNING_ENABLED and config['enabled'] and privacy_ready(config)


def audit(db,actor,action,kind,entity_id,old=None,new=None):
    # Only statuses, hashes and counters; never conversations, prompts or content.
    db.add(LearningAudit(actor_id=actor,action=action,entity_type=kind,entity_id=str(entity_id),
        previous_state=old or {},new_state=new or {}))


def change_settings(db,values,actor):
    row=config_row(db,True);old=configuration(db);new=validate_settings({**old,**values})
    if new['enabled'] and not privacy_ready(new):raise LearningError('Complete privacy, notice and provider reviews before enabling learning')
    row.config=new;row.updated_at=datetime.utcnow();row.updated_by=actor
    audit(db,actor,'SETTINGS_CHANGED','settings',1,{'revision':row.revision-1},{'revision':row.revision})
    return new


def source_change(db,conversation_id,allowed,actor,consent_reference='',legal_hold=False,erase=False,evaluation_only=False,messages_from=None):
    config_row(db,True)
    if db.get(WhatsAppConversation,conversation_id) is None:raise LearningError('Conversation not found')
    source=db.query(LearningSource).filter_by(conversation_id=conversation_id).one_or_none()
    if source is None:
        source=LearningSource(conversation_id=conversation_id,allowed=False);db.add(source);db.flush()
    if evaluation_only and not source.evaluation_only and db.query(LearningAnalysis.id).filter_by(source_id=source.id).first():
        raise LearningError('Evaluation sources must be reserved before analysis')
    if messages_from is not None:
        if messages_from!=source.messages_from and db.query(LearningAnalysis.id).filter_by(source_id=source.id).first():raise LearningError('Set message scope before analysis; existing derivatives require privacy review before changing scope')
        if source.messages_from and messages_from<source.messages_from:raise LearningError('Existing consent scope cannot be broadened')
        source.messages_from=messages_from
    source.evaluation_only=evaluation_only or source.evaluation_only
    may_erase=erase and not source.legal_hold and not legal_hold and not configuration(db)['legal_hold']
    if allowed:
        if not privacy_ready(configuration(db)) or not consent_reference.strip() or prohibited(consent_reference):
            raise LearningError('Reviewed privacy controls and a non-personal consent reference are required')
        source.allowed=True;source.consent_at=datetime.utcnow();source.consent_by=actor
        source.consent_reference=consent_reference[:200];source.withdrawn_at=None
    else:
        source.allowed=False;source.withdrawn_at=datetime.utcnow()
        db.flush()
        candidates={item.candidate_id for item in db.query(LearningEvidence).filter_by(source_id=source.id)}
        for cid in candidates:
            candidate=db.get(LearningCandidate,cid)
            if candidate.knowledge_entry_id:disable_knowledge(db,candidate,actor,'SOURCE_WITHDRAWN')
            evaluate(db,candidate,configuration(db))
            candidate.publication_status='REJECTED';candidate.validation_status='BLOCKED';candidate.rejection_reason='SOURCE_WITHDRAWN'
            if may_erase:
                candidate.proposed_content='';candidate.title='Withdrawn lesson'
                if candidate.knowledge_entry_id:
                    entry=db.get(BotKnowledgeEntry,candidate.knowledge_entry_id);entry.content=''
                    # Explicit privacy erasure is the only content-redaction exception
                    # to immutable version history. Hashes and audit remain intact.
                    for version in db.query(LearningKnowledgeVersion).filter_by(candidate_id=cid):version.content=''
        for case in db.query(LearningRegressionCase).filter_by(source_id=source.id):
            case.active=False
            if may_erase:case.sanitized_question='';case.source_message_references=[]
        if may_erase:
            for evidence in db.query(LearningEvidence).filter_by(source_id=source.id):
                evidence.sanitized_excerpt=[];evidence.source_message_references=[]
    source.legal_hold=legal_hold;source.updated_at=datetime.utcnow()
    audit(db,actor,'SOURCE_ALLOWED' if allowed else 'SOURCE_WITHDRAWN','source',source.id,new={'allowed':allowed,'erased':bool(may_erase)})
    return source


def approved_entries(db,exclude_knowledge_id=None):
    config=db.query(BotConfiguration).first();snapshot=config.published_snapshot if config else {}
    entries=[item for bucket in ('knowledge','policies','faqs') for item in snapshot.get(bucket,[]) if item.get('enabled',True)]
    for row in db.query(BotKnowledgeEntry).filter(BotKnowledgeEntry.enabled.is_(True),BotKnowledgeEntry.archived.is_(False)).limit(500):
        if row.id!=exclude_knowledge_id:entries.append({'title':row.title,'content':row.content})
    entries.append({'title':'Approved delivery policy','content':str(snapshot.get('delivery',{}))})
    return entries,snapshot


def evaluate(db,candidate,config):
    evidence=db.query(LearningEvidence).filter_by(candidate_id=candidate.id).all()
    eligible=[];supporting=set()
    phrase=next((key for key,value in COMMUNICATION.items() if value==candidate.proposed_content),None)
    for item in evidence:
        source=db.query(LearningSource).filter_by(id=item.source_id).populate_existing().one_or_none()
        if not source or not source.allowed or source.withdrawn_at or source.evaluation_only or not source.consent_at or not source.consent_reference or not item.sanitized_excerpt:continue
        eligible.append(item)
        if any(row.get('role')=='staff' and candidate.proposed_content in row.get('text','') for row in item.sanitized_excerpt):supporting.add(source.id)
    candidate.evidence_count=len({item.source_id for item in eligible})
    entries,snapshot=approved_entries(db,candidate.knowledge_entry_id)
    related=[];conflict=False
    for entry in entries:
        text=entry.get('content','')
        if not isinstance(text,str):continue
        if similar(candidate.proposed_content,text):
            related.append({'title':str(entry.get('title',''))[:120],'content':text[:500]})
            conflict=True  # Exact/near duplicates do not overwrite approved knowledge.
    if candidate.category in {'product','policy'}:
        for other in db.query(LearningCandidate).filter(LearningCandidate.id!=candidate.id,LearningCandidate.language==candidate.language).order_by(LearningCandidate.id.desc()).limit(100):
            if similar(candidate.proposed_content,other.proposed_content) and candidate.content_hash!=other.content_hash:
                related.append({'title':other.title,'content':other.proposed_content[:500]})
                if set(re.findall(r'\d+',candidate.proposed_content))!=set(re.findall(r'\d+',other.proposed_content)):conflict=True
    if candidate.category=='policy' and re.search(r'deliver|බෙදා',candidate.proposed_content,re.I):
        numbers={int(n) for n in re.findall(r'\b\d+\b',candidate.proposed_content)}
        for term,value in {'one':1,'two':2,'three':3,'four':4,'දෙක':2,'දෙදින':2,'තුන':3}.items():
            if term in candidate.proposed_content.casefold():numbers.add(value)
        days=snapshot.get('delivery',{})
        approved={days.get('minimum_working_days'),days.get('maximum_working_days')}
        if numbers and not numbers.issubset(approved):conflict=True
    checks={'privacy':not prohibited(candidate.proposed_content+' '+candidate.title),
        'security':candidate.risk_level!='PROHIBITED','eligible_evidence':bool(eligible),
        'no_volatile_facts':not bool(CURRENCY.search(candidate.proposed_content)),
        'approved_knowledge_compatible':not conflict,'bounded_content':0<len(candidate.proposed_content)<=1000}
    # Independent, code-reviewed cases are not the conversation extraction examples.
    regression_cases={'price_request':not CURRENCY.search(candidate.proposed_content),
        'security_request':not prohibited(candidate.proposed_content),
        'unicode_language':bool(candidate.proposed_content.strip())}
    # Manager-approved held-out conversations supplement code-reviewed content
    # policy cases. Never reuse a candidate's learning sources as evaluation data.
    learning_sources={item.source_id for item in evidence}
    heldout=db.query(LearningRegressionCase).join(LearningSource,LearningSource.id==LearningRegressionCase.source_id).filter(
        LearningRegressionCase.active.is_(True),LearningSource.allowed.is_(True),LearningSource.evaluation_only.is_(True),
        LearningSource.withdrawn_at.is_(None),LearningRegressionCase.language==candidate.language).limit(50).all()
    for case in heldout:
        if case.source_id in learning_sources:continue
        if case.expected_behavior=='SHOPIFY_AUTHORITY':result=not CURRENCY.search(candidate.proposed_content)
        elif case.expected_behavior=='SECURITY':result=not prohibited(candidate.proposed_content)
        else:result=candidate.proposed_content in COMMUNICATION.values() or candidate.review_required
        regression_cases['holdout_'+str(case.id)]=bool(result)
    checks.update(regression_cases)
    passed=sum(bool(value) for value in checks.values())
    auto_coverage=bool(phrase and phrase.endswith('.'+candidate.language) and len(supporting)>=config['min_evidence'])
    evaluation=LearningEvaluation(candidate_id=candidate.id,evaluation_version=EVALUATION_VERSION,
        content_hash=candidate.content_hash,test_case_count=len(checks),passed_count=passed,failed_count=len(checks)-passed,
        safety_result=checks['privacy'] and checks['security'],factuality_result=checks['no_volatile_facts'] and not conflict,
        regression_result=all(regression_cases.values()),evaluation_details={'checks':{k:bool(v) for k,v in checks.items()},
            'automatic_coverage':auto_coverage,'phrase':phrase,'related_knowledge':related,'conflict':conflict,
            'source_conversations':candidate.evidence_count,'independent_dataset':'communication-regression-v1'})
    db.add(evaluation);db.flush()
    candidate.validation_status='PASSED' if evaluation.failed_count==0 else 'BLOCKED'
    candidate.review_required=not (auto_coverage and candidate.risk_level=='LOW' and phrase in config['auto_allowlist'] and float(candidate.confidence_score)>=config['confidence'])
    return evaluation


def can_auto(db,candidate,evaluation,config):
    if candidate.rejection_reason:return False
    if not (learning_enabled(config) and settings.LEARNING_AUTOPUBLISH_ENABLED and settings.LEARNING_PUBLICATION_ENABLED
        and config['publication_enabled'] and config['auto_publish'] and not config['auto_paused']):return False
    phrase=evaluation.evaluation_details['phrase']
    if (evaluation.failed_count or not evaluation.evaluation_details['automatic_coverage'] or phrase not in config['auto_allowlist']
        or candidate.risk_level!='LOW' or float(candidate.confidence_score)<config['confidence']):return False
    # Colombo day boundaries, stored and queried in UTC.
    from learning_schedule import day_start
    count=db.query(LearningKnowledgeVersion).filter(LearningKnowledgeVersion.source_type=='AUTO',LearningKnowledgeVersion.created_at>=day_start()).count()
    return count<config['max_auto_per_day']


def publish_candidate(db,candidate,actor,automatic=False):
    config_row(db,True);config=configuration(db)
    candidate=db.query(LearningCandidate).filter_by(id=candidate.id).populate_existing().one()
    if not settings.LEARNING_PUBLICATION_ENABLED or not config['publication_enabled'] or not privacy_ready(config):
        raise LearningError('Knowledge publication is disabled')
    evaluation=evaluate(db,candidate,config)
    if evaluation.failed_count:raise LearningError('Mandatory evidence, privacy, conflict or factuality evaluation failed')
    if automatic:
        if not can_auto(db,candidate,evaluation,config):raise LearningError('Automatic promotion gates did not pass')
    elif candidate.publication_status!='APPROVED':raise LearningError('Manager approval is required')
    if candidate.product_handle:
        from shopify_catalog_service import get_product_by_handle
        try:product=get_product_by_handle(candidate.product_handle)
        except Exception:raise LearningError('Current Shopify verification is unavailable') from None
        if not product:raise LearningError('Product could not be verified in current Shopify data')
    entry=db.get(BotKnowledgeEntry,candidate.knowledge_entry_id) if candidate.knowledge_entry_id else None
    if entry is None:
        entry=BotKnowledgeEntry(kind='KNOWLEDGE',scope='PRODUCT' if candidate.product_handle else 'GLOBAL',
            scope_reference=candidate.product_handle,title=candidate.title,topic=candidate.category,content=candidate.proposed_content,
            tags='learned',enabled=False,archived=False)
        db.add(entry);db.flush();candidate.knowledge_entry_id=entry.id
    latest=db.query(LearningKnowledgeVersion).filter_by(knowledge_item_id=entry.id).order_by(LearningKnowledgeVersion.version_number.desc()).first()
    if latest and latest.content_hash==candidate.content_hash and latest.publication_status=='PUBLISHED' and entry.enabled:
        return latest
    version=LearningKnowledgeVersion(knowledge_item_id=entry.id,candidate_id=candidate.id,
        version_number=(latest.version_number+1 if latest else 1),content=candidate.proposed_content,content_hash=candidate.content_hash,
        source_type='AUTO' if automatic else 'MANAGER',publication_status='PUBLISHED',published_by=actor,
        evaluation_id=evaluation.id,rollback_reference=latest.id if latest else None)
    db.add(version);db.flush()
    entry.title=candidate.title;entry.content=candidate.proposed_content;entry.topic=candidate.category
    entry.scope='PRODUCT' if candidate.product_handle else 'GLOBAL';entry.scope_reference=candidate.product_handle
    intent=(candidate.analysis_summary or {}).get('intent','')
    entry.tags='learned '+(intent[:120] if not prohibited(intent) else '')
    entry.enabled=True;entry.archived=False
    candidate.publication_status='PUBLISHED';candidate.auto_published=automatic;candidate.updated_at=datetime.utcnow()
    audit(db,actor,'AUTO_PUBLISHED' if automatic else 'PUBLISHED','candidate',candidate.id,
        new={'version_id':version.id,'hash':candidate.content_hash})
    return version


def disable_knowledge(db,candidate,actor,reason='ROLLED_BACK'):
    entry=db.get(BotKnowledgeEntry,candidate.knowledge_entry_id)
    if entry is None:return None
    latest=db.query(LearningKnowledgeVersion).filter_by(knowledge_item_id=entry.id).order_by(LearningKnowledgeVersion.version_number.desc()).first()
    if not entry.enabled and latest and latest.publication_status=='ROLLED_BACK':return latest
    entry.enabled=False;candidate.publication_status='ROLLED_BACK';candidate.rejection_reason=reason
    version=LearningKnowledgeVersion(knowledge_item_id=entry.id,candidate_id=candidate.id,
        version_number=latest.version_number+1 if latest else 1,content='',content_hash=content_hash('',candidate.language),
        source_type='MANAGER' if actor else 'PRIVACY',publication_status='ROLLED_BACK',published_by=actor,
        rollback_reference=latest.id if latest else None)
    db.add(version);db.flush()
    audit(db,actor,reason,'candidate',candidate.id,new={'version_id':version.id})
    return version


def review(db,cid,action,actor,body):
    config_row(db,True);candidate=db.get(LearningCandidate,cid)
    if candidate is None:raise LearningError('Candidate not found')
    previous=candidate.publication_status
    if action=='edit':
        content=body.get('content','');title=body.get('title',candidate.title)
        if not isinstance(content,str) or not 0<len(content)<=1000 or not isinstance(title,str) or not 0<len(title)<=120:
            raise LearningError('Bounded content and title are required')
        if prohibited(content+' '+title) or sanitize(content)!=content or sanitize(title)!=title:raise LearningError('Sensitive/instructional content cannot be published')
        candidate.proposed_content=content;candidate.title=title
        candidate.category,candidate.risk_level=classify(content,candidate.category)
        candidate.content_hash=content_hash(content,candidate.language,candidate.product_handle)
        candidate.publication_status='DRAFT';candidate.review_required=True
        evaluate(db,candidate,configuration(db))
    elif action=='approve':
        evaluation=evaluate(db,candidate,configuration(db))
        if evaluation.failed_count:raise LearningError('Evaluation failed; edit or request evidence before approval')
        if body.get('verified') is not True:raise LearningError('Confirm evidence, language and business facts have been independently verified')
        if candidate.publication_status!='PUBLISHED':candidate.publication_status='APPROVED'
        candidate.reviewer_id=actor;candidate.reviewed_at=datetime.utcnow();candidate.rejection_reason=None
    elif action=='publish':publish_candidate(db,candidate,actor)
    elif action=='reject':
        if candidate.knowledge_entry_id:disable_knowledge(db,candidate,actor,'REJECTED')
        candidate.publication_status='REJECTED';candidate.rejection_reason='MANAGER_REJECTED'
    elif action=='more-evidence':candidate.publication_status='PENDING_REVIEW';candidate.rejection_reason='MORE_EVIDENCE_REQUIRED'
    elif action=='rollback':
        target=body.get('version_id')
        if target is None:disable_knowledge(db,candidate,actor)
        else:
            version=db.get(LearningKnowledgeVersion,target)
            if version is None or version.candidate_id!=cid or not version.content:raise LearningError('Invalid rollback target')
            candidate.proposed_content=version.content;candidate.content_hash=version.content_hash
            candidate.publication_status='APPROVED';candidate.reviewer_id=actor;candidate.reviewed_at=datetime.utcnow()
            restored=publish_candidate(db,candidate,actor);restored.rollback_reference=version.id
    else:raise LearningError('Unsupported review action')
    candidate.updated_at=datetime.utcnow()
    audit(db,actor,action.upper(),'candidate',cid,{'status':previous},{'status':candidate.publication_status,'hash':candidate.content_hash})
    return candidate


def prune(db):
    config=configuration(db)
    if config['legal_hold']:return
    now=datetime.utcnow();affected=set()
    for evidence in db.query(LearningEvidence).filter(LearningEvidence.created_at<now-timedelta(days=config['evidence_days'])):
        if not evidence.sanitized_excerpt and not evidence.source_message_references:continue
        source=db.get(LearningSource,evidence.source_id)
        if source and source.legal_hold:continue
        evidence.sanitized_excerpt=[];evidence.source_message_references=[];affected.add(evidence.candidate_id)
    db.flush()
    for cid in affected:
        candidate=db.get(LearningCandidate,cid)
        evaluate(db,candidate,config)
        if candidate.knowledge_entry_id and db.get(BotKnowledgeEntry,candidate.knowledge_entry_id).enabled:
            disable_knowledge(db,candidate,None,'EVIDENCE_EXPIRED')
    for case in db.query(LearningRegressionCase).filter(LearningRegressionCase.created_at<now-timedelta(days=config['evidence_days'])):
        if db.get(LearningSource,case.source_id).legal_hold:continue
        case.active=False;case.sanitized_question='';case.source_message_references=[]
    for row in db.query(LearningEvaluation).filter(LearningEvaluation.created_at<now-timedelta(days=config['evaluation_days'])):row.evaluation_details={}
    for row in db.query(LearningJob).filter(LearningJob.created_at<now-timedelta(days=config['job_log_days'])):row.error_summary=None
    for row in db.query(LearningAudit).filter(LearningAudit.timestamp<now-timedelta(days=config['audit_days'])):
        row.actor_id=None;row.previous_state={};row.new_state={}
    for row in db.query(LearningCandidate).filter(LearningCandidate.updated_at<now-timedelta(days=config['candidate_days']),LearningCandidate.publication_status.in_(['DRAFT','REJECTED','ROLLED_BACK','PENDING_REVIEW'])):
        if any(db.get(LearningSource,e.source_id).legal_hold for e in db.query(LearningEvidence).filter_by(candidate_id=row.id)):continue
        row.proposed_content='';row.title='Expired suggestion';row.publication_status='REJECTED';row.rejection_reason='RETENTION_EXPIRED'


def enqueue(db,key,kind='MANUAL'):
    config_row(db,True);config=configuration(db)
    existing=db.query(LearningJob).filter_by(request_key=key).one_or_none()
    if existing:return existing
    if not learning_enabled(config):raise LearningError('Learning is disabled or required privacy controls are not reviewed')
    if db.query(LearningJob).filter(LearningJob.status.in_(['QUEUED','RUNNING','RETRY'])).first():raise LearningError('A learning job is already pending')
    if kind=='MANUAL' and db.query(LearningJob).filter(LearningJob.job_type=='MANUAL',LearningJob.created_at>=datetime.utcnow()-timedelta(hours=24)).count()>=3:
        raise LearningError('Manual run limit reached')
    from learning_diagnostics import environment
    job=LearningJob(id=str(uuid4()),request_key=key,job_type=kind,status='QUEUED',diagnostics={'queued_environment':environment(),'skip_reasons':{},'sources_evaluated':0,'sources_skipped':0,'conversations_selected':0,'eligible_human_messages':0,'eligible_customer_messages':0,'excluded_messages':0})
    db.add(job);db.flush();audit(db,None,'JOB_QUEUED','job',job.id)
    return job
