"""Phase7A reproductions: genuine batches, skip attribution and privacy-safe diagnostics."""
from datetime import datetime,timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event
from learning_models import LearningSource,LearningJob,LearningCandidate,LearningAnalysis,LearningSettings
from learning_service import enqueue,config_row,configuration,source_change,LearningError
from learning_diagnostics import classify_message,diagnose
import learning_worker as worker
from learning_analyzer import AnalysisFailure
from models import WhatsAppConversation,WhatsAppConversationMessage
from settings import settings
from test_learning_center import learning,source,enable,extractor


def test_recent_authorized_source_is_explained_not_silently_successful(learning,monkeypatch):
    client,sessions,engine,_,_,_=learning;enable(sessions,monkeypatch);cid=source(sessions)
    with sessions() as db:
        db.get(WhatsAppConversation,cid).last_message_at=datetime.utcnow()
        row=config_row(db);row.config={**configuration(db),'quiet_hours':24};db.commit()
    worker.tick(engine,sessions,lambda *a:pytest.fail('Quiet period was bypassed'))
    with sessions() as db:
        job=db.query(LearningJob).one();assert job.status=='COMPLETED' and job.conversations_processed==0
        assert job.diagnostics['authorized_sources']==1 and job.diagnostics['sources_evaluated']==1
        assert job.diagnostics['skip_reasons']=={'QUIET_PERIOD':1}
        jid=job.id
    response=client.get(f'/crm/bot-management/learning/api/jobs/{jid}/diagnostics')
    assert response.status_code==200 and '9477000' not in response.text and 'Person1' not in response.text
    assert 'sanitized_excerpt' not in response.text
    data=client.get('/crm/bot-management/learning/api/dashboard').json()
    assert data['latest_run']['summary']['outcome']=='NO_ELIGIBLE_CONVERSATIONS'


def test_real_conversation_ids_current_ai_mode_and_media_gap_do_not_hide_human_evidence(learning,monkeypatch):
    _,sessions,engine,_,_,_=learning;enable(sessions,monkeypatch);cid=source(sessions)
    with sessions() as db:
        conversation=db.get(WhatsAppConversation,cid);conversation.mode='AI'
        rows=db.query(WhatsAppConversationMessage).order_by(WhatsAppConversationMessage.id).all()
        customer,staff=rows;db.delete(staff);db.flush()
        for _ in range(8):db.add(WhatsAppConversationMessage(phone_number=conversation.phone_number,direction='outbound',response_kind='ai',message_text='Automatic response'))
        db.flush();cursor=db.query(WhatsAppConversationMessage).order_by(WhatsAppConversationMessage.id.desc()).first().id
        db.add(WhatsAppConversationMessage(phone_number=conversation.phone_number,direction='outbound',response_kind='staff',sent_by='learning-staff',send_status='delivered',message_text='Hello! How can I help you today?'))
        learning_source=db.query(LearningSource).one();learning_source.id=71;learning_source.last_message_id=cursor;learning_source.analyzer_version=worker.analysis_version(configuration(db));db.commit()
    captured=[]
    def analyze(window,model):captured.append(window);return extractor(window,model)
    worker.tick(engine,sessions,analyze)
    assert len(captured)==1 and any(row['role']=='customer' for row in captured[0]['messages'])
    with sessions() as db:
        assert db.query(LearningJob).one().conversations_processed==1
        candidate=db.query(LearningCandidate).one();assert candidate.publication_status=='PENDING_REVIEW' and candidate.evidence_count==1
        assert db.query(LearningJob).one().diagnostics['candidate_reasons']['INSUFFICIENT_EVIDENCE']==1
        assert db.query(LearningJob).one().diagnostics['sources'][0]['conversation_id']==cid
        assert db.query(LearningJob).one().diagnostics['sources'][0]['source_id']==71


@pytest.mark.parametrize('direction,kind,sender,status,media,expected',[
    ('inbound',None,None,None,None,'customer'),('inbound','customer',None,None,None,'customer'),
    ('outbound','staff','learning-staff','delivered',None,'staff'),
    ('outbound','staff','learning-staff','read',None,'staff'),
    ('outbound','staff_product','learning-staff','sent',None,'staff_context'),
    ('outbound','staff_audio','learning-staff','sent','fake','media'),
    ('outbound','staff_media','learning-staff','sent','fake','media'),
    ('outbound','handover',None,'sent',None,'system'),
    ('inbound','customer_media',None,None,'fake','media'),
    ('inbound','customer_audio',None,None,'fake','media'),
    ('outbound','ai',None,'sent',None,'ai'),
    ('outbound','staff',None,'sent',None,'unknown'),
    ('outbound',None,'learning-staff','sent',None,'unknown'),
    ('status',None,None,None,None,'system'),('outbound','staff','learning-staff','failed',None,'undelivered'),
])
def test_message_origin_cannot_be_inferred_from_direction(direction,kind,sender,status,media,expected):
    message=SimpleNamespace(direction=direction,response_kind=kind,sent_by=sender,send_status=status,media_id=media)
    assert classify_message(message,{'learning-staff'})==expected


@pytest.mark.parametrize('reason',['CONSENT_REQUIRED','SOURCE_WITHDRAWN','EVALUATION_ONLY'])
def test_source_restrictions_remain_authoritative(learning,monkeypatch,reason):
    _,sessions,engine,_,_,_=learning;enable(sessions,monkeypatch);source(sessions)
    with sessions() as db:
        row=db.query(LearningSource).one()
        if reason=='CONSENT_REQUIRED':row.allowed=False
        if reason=='SOURCE_WITHDRAWN':row.withdrawn_at=datetime.utcnow()
        if reason=='EVALUATION_ONLY':row.evaluation_only=True
        db.commit()
    worker.tick(engine,sessions,lambda *a:pytest.fail('Restricted source analyzed'))
    with sessions() as db:
        job=db.query(LearningJob).one();assert reason in job.diagnostics['skip_reasons']
        assert job.conversations_processed==0


def test_explicit_scope_excludes_history_and_cannot_be_broadened(learning,monkeypatch):
    client,sessions,engine,actor,_,write=learning;enable(sessions,monkeypatch);cid=source(sessions)
    cutoff=datetime.utcnow()-timedelta(hours=1)
    with sessions() as db:
        source_change(db,cid,True,actor,'notice-v1',messages_from=cutoff);db.commit()
    worker.tick(engine,sessions,lambda *a:pytest.fail('Historical scope was expanded'))
    with sessions() as db:
        assert db.query(LearningJob).one().diagnostics['skip_reasons']['CONSENT_SCOPE_EXCLUDED']==1
        assert db.query(LearningSource).one().last_message_id==0
    response=write('/sources',{'conversation_id':cid,'allowed':True,'consent_reference':'notice-v1','messages_from':(cutoff-timedelta(hours=1)).isoformat()+'Z'})
    assert response.status_code==409
    with sessions() as db:
        conversation=db.get(WhatsAppConversation,cid)
        db.add_all([WhatsAppConversationMessage(phone_number=conversation.phone_number,direction='inbound',message_text='Hi there',response_kind='customer'),
            WhatsAppConversationMessage(phone_number=conversation.phone_number,direction='outbound',message_text='Hello! How can I help you today?',response_kind='staff',sent_by='learning-staff',send_status='sent')]);db.commit()
    worker.tick(engine,sessions,extractor)
    with sessions() as db:assert db.query(LearningCandidate).count()==1


def test_no_new_messages_no_authorized_sources_and_read_only_diagnosis(learning,monkeypatch):
    client,sessions,engine,_,_,_=learning;enable(sessions,monkeypatch)
    worker.tick(engine,sessions,lambda *a:pytest.fail('No sources'))
    with sessions() as db:assert db.query(LearningJob).one().diagnostics['skip_reasons']['NO_AUTHORIZED_SOURCES']==1
    source(sessions)
    with sessions() as db:db.query(WhatsAppConversationMessage).delete();db.commit()
    worker.tick(engine,sessions,lambda *a:pytest.fail('No new messages'))
    queries=[]
    def record(conn,cursor,sql,*a):queries.append(sql)
    event.listen(engine,'before_cursor_execute',record)
    try:
        with sessions() as db:
            result=diagnose(db);assert result['sources'][0]['reason']=='NO_NEW_MESSAGES';db.rollback()
    finally:event.remove(engine,'before_cursor_execute',record)
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in queries)


def test_failed_extraction_retains_cursor_and_counts_failure_not_success(learning,monkeypatch):
    _,sessions,engine,_,_,_=learning;enable(sessions,monkeypatch);source(sessions)
    worker.tick(engine,sessions,lambda *a:(_ for _ in ()).throw(AnalysisFailure('invalid_output')))
    with sessions() as db:
        assert db.query(LearningSource).one().last_message_id==0
        job=db.query(LearningJob).one();assert job.status=='PARTIAL' and job.error_count==1 and job.conversations_processed==0
        assert job.diagnostics['skip_reasons']['EXTRACTION_FAILED']==1
    worker.tick(engine,sessions,extractor)
    with sessions() as db:assert db.query(LearningCandidate).count()==1


def test_worker_web_environment_mismatch_marks_queued_job(learning,monkeypatch):
    client,sessions,engine,_,_,write=learning;enable(sessions,monkeypatch);source(sessions)
    response=write('/run',{});assert response.status_code==200;jid=response.json()['job_id']
    monkeypatch.setattr(settings,'LEARNING_ENABLED',False)
    assert worker.tick(engine,sessions,lambda *a:pytest.fail('Disabled worker'))=='DISABLED'
    with sessions() as db:
        job=db.get(LearningJob,jid);assert job.status=='PAUSED_CONFIG' and not job.conversations_processed
        assert job.diagnostics['skip_reasons']=={'LEARNING_DISABLED':1,'CONFIGURATION_MISMATCH':1}
        assert 'OPENAI_API_KEY' not in str(job.diagnostics) and 'SECRET_KEY' not in str(job.diagnostics)


def test_already_current_sources_do_not_starve_later_valid_sources(learning,monkeypatch):
    _,sessions,engine,_,_,_=learning;enable(sessions,monkeypatch)
    for number in range(1,13):
        cid=source(sessions,number)
        with sessions() as db:
            row=db.query(LearningSource).filter_by(conversation_id=cid).one()
            conversation=db.get(WhatsAppConversation,cid)
            row.last_message_id=db.query(WhatsAppConversationMessage).filter_by(phone_number=conversation.phone_number).order_by(WhatsAppConversationMessage.id.desc()).first().id
            row.analyzer_version=worker.analysis_version(configuration(db));db.commit()
    source(sessions,13)
    worker.tick(engine,sessions,extractor)
    with sessions() as db:
        job=db.query(LearningJob).one();assert job.conversations_processed==1
        assert job.diagnostics['sources_evaluated']==13 and job.diagnostics['skip_reasons']['NO_NEW_MESSAGES']==12


def test_diagnostics_permissions_and_historical_runs_are_not_reconstructed(learning):
    client,sessions,_,_,login,_=learning
    with sessions() as db:
        job=LearningJob(id=str(uuid4()),request_key=str(uuid4()),job_type='MANUAL',status='COMPLETED');db.add(job);db.commit();jid=job.id
    data=client.get(f'/crm/bot-management/learning/api/jobs/{jid}/diagnostics').json()
    assert data['historical_available'] is False and data['diagnostics']=={}
    login('STAFF')
    assert client.get(f'/crm/bot-management/learning/api/jobs/{jid}/diagnostics').status_code==403
    assert client.get('/crm/bot-management/learning/api/diagnostics/sources').status_code==403
    assert 'diagnostics' not in client.get(f'/crm/bot-management/learning/api/jobs/{jid}').json()


def test_chromium_zero_run_history_diagnostics_and_mobile(learning,monkeypatch):
    import shutil
    from urllib.parse import urlsplit
    playwright=pytest.importorskip('playwright.sync_api');chromium=shutil.which('chromium')
    if not chromium:pytest.skip('Chromium unavailable')
    client,sessions,engine,_,_,_=learning;enable(sessions,monkeypatch);cid=source(sessions)
    with sessions() as db:
        row=config_row(db);row.config={**configuration(db),'quiet_hours':24}
        db.get(WhatsAppConversation,cid).last_message_at=datetime.utcnow();db.commit()
    worker.tick(engine,sessions,lambda *a:pytest.fail('Quiet source analyzed'))
    with playwright.sync_playwright() as api:
        browser=api.chromium.launch(executable_path=chromium,headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={'width':1200,'height':900});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def serve(route):
                request=route.request;parsed=urlsplit(request.url)
                if parsed.hostname!='inbox.test':route.fulfill(status=200,body='');return
                result=client.request(request.method,parsed.path+('?' + parsed.query if parsed.query else ''),follow_redirects=False)
                route.fulfill(status=result.status_code,body=result.content,headers={k:v for k,v in result.headers.items() if k not in {'content-length','content-encoding','set-cookie'}})
            page.route('**/*',serve);page.goto('https://inbox.test/crm/bot-management/learning')
            page.locator('#learning-latest-run').filter(has_text='QUIET_PERIOD').wait_for()
            page.locator('[data-tab="history"]').click()
            page.locator('#learning-jobs').filter(has_text='Completed — No eligible conversations found').wait_for()
            page.get_by_role('button',name='View Diagnostics').click()
            page.locator('#learning-diagnostics-sources').filter(has_text='QUIET_PERIOD').wait_for()
            assert 'Person1' not in page.locator('#learning-run-diagnostics').inner_text()
            assert '9477000' not in page.locator('#learning-run-diagnostics').inner_text()
            page.set_viewport_size({'width':375,'height':800})
            assert page.locator('#learning-run-diagnostics').is_visible() and not errors
        finally:browser.close()


def test_additive_migration_preserves_existing_job_and_consent_records(learning):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import text,Table,MetaData
    _,sessions,engine,_,_,_=learning;cid=source(sessions)
    with sessions() as db:original=db.query(LearningSource).one().consent_at
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE learning_jobs DROP COLUMN diagnostics'))
        connection.execute(text('ALTER TABLE learning_sources DROP COLUMN messages_from'))
        table=Table('learning_jobs',MetaData(),autoload_with=connection)
        values={key:0 for key in ['checkpoint','conversations_processed','candidates_generated','candidates_rejected','candidates_auto_published','candidates_pending_review','token_usage','reserved_tokens','estimated_cost','error_count','attempts']}
        connection.execute(table.insert().values(**values,id='retained-job',request_key='retained-request',job_type='MANUAL',status='COMPLETED',created_at=datetime.utcnow()))
    config=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));command.stamp(config,'20261025_14')
    command.upgrade(config,'head');command.upgrade(config,'head')
    with sessions() as db:
        job=db.get(LearningJob,'retained-job');assert job.status=='COMPLETED' and job.diagnostics=={} and job.token_usage==0
        row=db.query(LearningSource).one();assert row.consent_at==original and row.messages_from is None and row.conversation_id==cid


def test_non_human_prefix_windows_do_not_hide_later_genuine_replies(learning,monkeypatch):
    _,sessions,engine,_,_,_=learning;enable(sessions,monkeypatch);cid=source(sessions)
    with sessions() as db:
        conversation=db.get(WhatsAppConversation,cid)
        db.query(WhatsAppConversationMessage).delete()
        db.add(WhatsAppConversationMessage(phone_number=conversation.phone_number,direction='inbound',response_kind='customer',message_text='Hi there'));db.flush()
        for _ in range(10):db.add(WhatsAppConversationMessage(phone_number=conversation.phone_number,direction='outbound',response_kind='ai',message_text='Automated reply'))
        db.flush();db.add(WhatsAppConversationMessage(phone_number=conversation.phone_number,direction='outbound',response_kind='staff',sent_by='learning-staff',send_status='read',message_text='Hello! How can I help you today?'));db.commit()
    calls=[]
    def analyzer(window,model):calls.append(1);return extractor(window,model)
    worker.tick(engine,sessions,analyzer)
    with sessions() as db:
        job=db.query(LearningJob).one();assert job.conversations_processed==1 and len(calls)==1
        assert job.diagnostics['conversations_selected']==1
        assert job.diagnostics['eligible_customer_messages']==1 and job.diagnostics['eligible_human_messages']==1
