"""Read-only authoritative metadata and actual responsive Details interactions."""
from datetime import datetime
import shutil
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

import main
from learning_models import LearningSource, LearningSettings, LearningJob, LearningAnalysis
from models import WhatsAppConversation
from settings import settings
from test_whatsapp_quick_messages import inbox


@pytest.mark.parametrize('mode', ['AI', 'HUMAN'])
def test_actual_id_profile_takeover_and_no_side_effects(inbox, mode):
    client, sessions, ids, sends, _, _ = inbox
    with sessions() as db:
        conversation = db.get(WhatsAppConversation, ids[0]); conversation.mode = mode
        conversation.phone_number = '94712345678'; conversation.taken_over_by = 'quick-staff'
        db.commit(); engine = db.get_bind()
    queries = []
    def record(conn, cursor, sql, *args): queries.append(sql)
    event.listen(engine, 'before_cursor_execute', record)
    try: response = client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/details')
    finally: event.remove(engine, 'before_cursor_execute', record)
    assert response.status_code == 200 and response.headers['cache-control'] == 'private, no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'
    data = response.json()['conversation']
    assert data['id'] == ids[0] and data['id'] != int(data['phone_number'])
    assert data['customer_name'] == 'Quick customer 1' and data['mode'] == mode
    assert data['phone_country']['name'] == 'Sri Lanka'
    assert data['assigned_staff'] is None and data['taken_over_by'] == 'quick-staff'
    assert data['learning'] == {'eligibility': 'EXCLUDED', 'engine_enabled': False,
        'analysis_status': None, 'job_status': None, 'analysis_completed_at': None}
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in queries)
    assert not any('learning_evidence' in sql.lower() or 'learning_candidates' in sql.lower() for sql in queries)
    with sessions() as db: assert db.get(WhatsAppConversation, ids[0]).unread_count == 2
    assert not sends


@pytest.mark.parametrize('allowed,consent,withdrawn,holdout,expected', [
    (False, False, False, False, 'EXCLUDED'), (True, False, False, False, 'EXCLUDED'),
    (True, True, False, False, 'ELIGIBLE'), (True, True, False, True, 'EVALUATION_ONLY'),
    (True, True, True, False, 'WITHDRAWN'),
])
def test_learning_eligibility_comes_from_source_not_ai_mode(inbox, monkeypatch, allowed, consent, withdrawn, holdout, expected):
    client, sessions, ids, _, _, _ = inbox
    monkeypatch.setattr(settings, 'LEARNING_ENABLED', True)
    with sessions() as db:
        config = db.get(LearningSettings, 1)
        config.config = {**config.config, 'enabled': True, 'privacy_reviewed': True, 'notice_configured': True, 'provider_reviewed': True}
        source = LearningSource(conversation_id=ids[0], allowed=allowed, evaluation_only=holdout,
            consent_reference='private-notice-reference' if consent else None,
            consent_at=datetime.utcnow() if consent else None, withdrawn_at=datetime.utcnow() if withdrawn else None)
        db.add(source); db.flush()
        job = LearningJob(id=str(uuid4()), request_key=str(uuid4()), job_type='MANUAL', status='COMPLETED', error_summary='private-job-error')
        db.add(job); db.flush()
        db.add(LearningAnalysis(source_id=source.id, job_id=job.id, end_message_id=123,
            analyzer_version='1', status='COMPLETE', completed_at=datetime(2026, 10, 24, 20, 30), error_code='private-analysis-error'))
        db.commit()
    response = client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/details')
    learning = response.json()['conversation']['learning']
    assert learning['eligibility'] == expected and learning['engine_enabled'] is True
    assert learning['analysis_status'] == 'COMPLETE' and learning['job_status'] == 'COMPLETED'
    assert learning['analysis_completed_at'] == '2026-10-24T20:30:00Z'
    assert 'private-' not in response.text and 'consent_reference' not in response.text
    assert 'evidence' not in response.text and 'source_message' not in response.text
    other = client.get(f'/crm/whatsapp/api/conversations/{ids[1]}/details').json()['conversation']
    assert other['learning']['analysis_status'] is None and other['learning']['eligibility'] == 'EXCLUDED'


@pytest.mark.parametrize('name', ['quick-admin', 'quick-manager', 'quick-staff', 'quick-agent'])
def test_existing_staff_roles_can_read_details(inbox, name):
    client, _, ids, _, login, _ = inbox; login(name)
    assert client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/details').status_code == 200


def test_auth_permissions_missing_conversation_and_invalid_phone(inbox):
    client, sessions, ids, _, login, _ = inbox
    assert TestClient(main.app).get(f'/crm/whatsapp/api/conversations/{ids[0]}/details', follow_redirects=False).status_code == 303
    login('quick-denied')
    assert client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/details').status_code == 403
    login()
    assert client.get('/crm/whatsapp/api/conversations/99999999/details').status_code == 404
    assert client.get('/crm/whatsapp/api/conversations/not-an-id/details').status_code == 422
    with sessions() as db:
        row = db.get(WhatsAppConversation, ids[0]); row.phone_number = 'invalid'; row.customer_name = ''; db.commit()
    data = client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/details').json()['conversation']
    assert data['phone_country'] is None and data['customer_name'] == 'WhatsApp customer'


def test_browser_details_loading_retry_copy_responsive_escape_and_switching(inbox):
    playwright = pytest.importorskip('playwright.sync_api'); chromium = shutil.which('chromium')
    if not chromium: pytest.skip('Chromium unavailable')
    client, sessions, ids, sends, _, _ = inbox
    attack = '<img src=x onerror="window.detailsInjected=true">'
    with sessions() as db:
        row = db.get(WhatsAppConversation, ids[0]); row.customer_name = attack; row.phone_number = '94712345678'; db.commit()
    with playwright.sync_playwright() as browser_api:
        browser = browser_api.chromium.launch(executable_path=chromium, headless=True, args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width': 1366, 'height': 900}); errors = []; held = []; behavior = {'hold': True, 'fail': False}
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script('Object.defineProperty(navigator,"clipboard",{value:{writeText:async value=>{window.copiedId=value;}}});')
            def serve(route):
                request = route.request; parsed = urlsplit(request.url)
                if parsed.hostname != 'inbox.test': route.fulfill(status=200, body=''); return
                if parsed.path.endswith('/details'):
                    if behavior['hold']: held.append(route); return
                    if behavior['fail']: route.fulfill(status=503, json={'detail': 'unavailable'}); return
                response = client.request(request.method, parsed.path + ('?' + parsed.query if parsed.query else ''), follow_redirects=False)
                route.fulfill(status=response.status_code, body=response.content, headers={k: v for k, v in response.headers.items() if k not in {'content-length', 'content-encoding', 'set-cookie'}})
            page.route('**/*', serve)
            page.goto(f'https://inbox.test/crm/whatsapp?conversation={ids[0]}')
            page.locator('#profile').filter(has_text=attack).wait_for()
            page.locator('#details-action').click()
            dialog = page.locator('#conversation-details-dialog')
            assert dialog.is_visible() and page.locator('#details-action').get_attribute('aria-expanded') == 'true'
            page.locator('#conversation-details-status').filter(has_text='Loading').wait_for()
            assert page.locator('#conversation-details-content').get_attribute('aria-busy') == 'true'
            page.wait_for_function('true')  # Flush route callbacks before completing the held request.
            assert len(held) == 1
            behavior['hold'] = False
            response = client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/details')
            held.pop().fulfill(status=200, json=response.json())
            page.locator('#conversation-internal-id').filter(has_text=str(ids[0])).wait_for()
            assert attack in dialog.inner_text() and dialog.locator('img').count() == 0
            assert not page.evaluate('Boolean(window.detailsInjected)')
            page.get_by_role('button', name='Copy internal conversation ID').click()
            page.locator('#conversation-details-status').filter(has_text='copied').wait_for()
            assert page.evaluate('window.copiedId') == str(ids[0])
            page.keyboard.press('Escape'); assert not dialog.is_visible()
            assert page.locator('#details-action').evaluate('(node)=>node===document.activeElement')
            behavior['fail'] = True; page.locator('#details-action').click()
            page.locator('#conversation-details-status').filter(has_text='Could not load').wait_for()
            assert page.locator('#conversation-details-content').inner_text() == ''
            behavior['fail'] = False; page.locator('#conversation-details-retry').click()
            page.locator('#conversation-internal-id').wait_for()
            page.evaluate('() => {navigator.clipboard.writeText=async()=>{throw new Error("denied")};}')
            page.get_by_role('button', name='Copy internal conversation ID').click()
            page.locator('#conversation-details-status').filter(has_text='copy it manually').wait_for()
            for width in [850, 390]:
                page.set_viewport_size({'width': width, 'height': 700})
                bounds = dialog.bounding_box(); assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
                page.keyboard.press('Tab')
                assert page.evaluate('Boolean(document.activeElement.closest("#conversation-details-dialog"))')
            page.locator('#conversation-details-order').click()
            assert page.locator('#details').is_visible()
            page.locator('#close-details').click()
            page.set_viewport_size({'width': 1366, 'height': 900})
            page.evaluate('''() => {
                const original = window.fetch;
                window.fetch = (url, options) => {
                    if (url.endsWith('/details') && !window.detailsDelayedOnce) {
                        window.detailsDelayedOnce = true;
                        return new Promise(resolve => {window.resolveOldDetails = resolve;});
                    }
                    return original(url, options);
                };
            }''')
            page.locator('#details-action').click()
            page.wait_for_function('Boolean(window.resolveOldDetails)')
            # Simulate a switch while the old response ignores cancellation.
            page.locator('.conversation-row').filter(has_text='Quick customer 2').evaluate('(node)=>node.click()')
            page.locator('#profile').filter(has_text='Quick customer 2').wait_for()
            page.locator('#conversation-internal-id').filter(has_text=str(ids[1])).wait_for()
            page.evaluate('(data)=>window.resolveOldDetails(new Response(JSON.stringify(data), {status:200}))', response.json())
            page.evaluate('() => new Promise(resolve => setTimeout(resolve, 0))')
            assert page.locator('#conversation-internal-id').inner_text() == str(ids[1])
            assert 'Quick customer 2' in dialog.inner_text() and attack not in dialog.inner_text()
            assert not errors and not sends
        finally: browser.close()


@pytest.mark.parametrize('master,enabled,privacy,expected', [
    (False, True, True, False), (True, False, True, False),
    (True, True, False, False), (True, True, True, True),
])
def test_learning_engine_requires_all_authoritative_gates(inbox, monkeypatch, master, enabled, privacy, expected):
    client, sessions, ids, _, _, _ = inbox
    monkeypatch.setattr(settings, 'LEARNING_ENABLED', master)
    with sessions() as db:
        row = db.get(LearningSettings, 1)
        row.config = {**row.config, 'enabled': enabled, 'privacy_reviewed': privacy,
            'notice_configured': privacy, 'provider_reviewed': privacy}; db.commit()
    data = client.get(f'/crm/whatsapp/api/conversations/{ids[0]}/details').json()['conversation']['learning']
    assert data['engine_enabled'] is expected
    assert data['eligibility'] == 'EXCLUDED'  # Global enablement never grants source consent.


def test_postgresql_read_only_details_query_uses_real_conversation_and_analysis(monkeypatch):
    import os
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.schema import CreateSchema, DropSchema
    from models import Base
    from learning_policy import DEFAULTS
    from conversation_details_service import conversation_details
    url = os.getenv('TEST_POSTGRESQL_URL')
    if not url: pytest.skip('Dedicated TEST_POSTGRESQL_URL not configured')
    assert make_url(url).database == 'pixiepinks_test'
    admin = create_engine(url); schema = 'details_' + uuid4().hex
    with admin.begin() as conn: conn.execute(CreateSchema(schema))
    engine = create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    sessions = sessionmaker(bind=engine)
    try:
        Base.metadata.create_all(engine)
        monkeypatch.setattr(settings, 'LEARNING_ENABLED', False)
        with sessions() as db:
            db.add(LearningSettings(id=1, config=DEFAULTS.copy()))
            conversation = WhatsAppConversation(id=73, phone_number='14165550123', mode='AI', customer_name='Fixture customer')
            db.add(conversation); db.flush()
            source = LearningSource(conversation_id=73, allowed=True, consent_reference='test-notice', consent_at=datetime.utcnow())
            db.add(source); db.flush()
            job = LearningJob(id=str(uuid4()), request_key=str(uuid4()), job_type='MANUAL', status='RETRY')
            db.add(job); db.flush()
            db.add(LearningAnalysis(source_id=source.id, job_id=job.id, end_message_id=1, analyzer_version='1', status='PROCESSING'))
            db.commit()
            statements = []
            def record(conn, cursor, sql, *args): statements.append(sql)
            event.listen(engine, 'before_cursor_execute', record)
            try: result = conversation_details(db, db.get(WhatsAppConversation, 73))
            finally: event.remove(engine, 'before_cursor_execute', record)
            assert result['id'] == 73 and result['phone_country']['region'] == 'CA'
            assert result['learning'] == {'eligibility': 'ELIGIBLE', 'engine_enabled': False,
                'analysis_status': 'PROCESSING', 'job_status': 'RETRY', 'analysis_completed_at': None}
            assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
    finally:
        engine.dispose()
        with admin.begin() as conn: conn.execute(DropSchema(schema, cascade=True))
        admin.dispose()
