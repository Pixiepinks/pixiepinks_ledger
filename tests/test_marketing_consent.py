"""Phase 1: consent evidence and staff-assisted requests; provider calls mocked."""
from datetime import datetime,timedelta
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
import main
import marketing_consent_service as service
from marketing_consent_constants import SCOPE,WORDING
from marketing_identity import phone
from models import (MarketingConsent as Consent,MarketingConsentEvent as Event,MarketingConsentRequest as ConsentRequest,
    Customer,Lead,WhatsAppConversation,WhatsAppConversationMessage,ProcessedWhatsAppMessage,User,GlobalAIReplyControl)
from test_whatsapp_quick_messages import inbox


@pytest.fixture
def consent(inbox):
    client,sessions,ids,sends,login,csrf=inbox
    with sessions() as db:
        db.get(GlobalAIReplyControl,1).enabled=False;db.commit()
        row=db.query(Consent).filter_by(conversation_id=ids[0]).one();contact=row.id
        users={u.username:u.id for u in db.query(User)}
    return SimpleNamespace(client=client,sessions=sessions,ids=ids,sends=sends,login=login,csrf=csrf,contact=contact,users=users)


def action(v,action,**body):
    with v.sessions() as db:version=db.get(Consent,v.contact).version
    return v.client.post(f'/crm/marketing-consent/api/{v.contact}/action',json={'action':action,'version':version,'key':str(uuid4()),**body},headers={'X-CSRF-Token':v.csrf})


def evidence(source='STAFF_VERIFIED_EXTERNAL'):
    return {'source':source,'scope':SCOPE,'affirmative':True,'source_description':'Signed customer form, verified by administrator.',
        'evidence_at':(datetime.utcnow()-timedelta(minutes=1)).isoformat()+'Z','wording':'I agree to PixiePinks WhatsApp promotions and may withdraw anytime.',
        'wording_version':'form-v1','evidence_type':'EXTERNAL_REFERENCE','evidence_reference':'secure-form:fixture-1','reason':'Verified original affirmative evidence.'}


def prepare(v,language='en',key=None):
    return v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/marketing-consent/request',json={'language':language,'key':key or str(uuid4())},headers={'X-CSRF-Token':v.csrf})


def send_request(v,preview):
    return v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/send',json={'message':preview['text'],'marketing_request_id':preview['request_id']},
        headers={'X-CSRF-Token':v.csrf,'Idempotency-Key':preview['key']})


def incoming(v,body,reply=None,key=None):
    provider=key or 'wamid.test.'+uuid4().hex
    with v.sessions() as db:phone_number=db.get(WhatsAppConversation,v.ids[0]).phone_number
    return main._store_conversation_message(phone_number,'inbound',body,whatsapp_message_id=provider,
        reply_to_whatsapp_message_id=reply,processed_record=ProcessedWhatsAppMessage(message_id=provider,sender_phone=phone_number,message_type='text',received_at=datetime.utcnow()))


def test_baseline_dedup_identity_and_no_ad_purchase_inference(consent):
    v=consent
    with v.sessions() as db:
        c=db.get(WhatsAppConversation,v.ids[0])
        db.add(Customer(name='Canonical customer',phone='+94 77 001 2301'))
        db.add(Lead(customer_name='Facebook inquiry',mobile=c.phone_number,source='Facebook',status='PAID'));db.commit()
        row=db.get(Consent,v.contact)
        assert row.status=='NOT_REQUESTED' and row.customer_id and not service.eligibility(db,row)['eligible']
        assert db.query(Consent).filter_by(phone_e164=phone(c.phone_number)).count()==1
        assert db.query(Customer).count()==1 and db.query(Lead).one().status=='PAID'
        assert not db.query(Event).count()
    assert not v.sends


def test_manual_verified_evidence_interests_withdrawal_and_import_suppression(consent):
    v=consent
    assert action(v,'verify',**evidence()).status_code==200
    with v.sessions() as db:assert service.eligibility(db,db.get(Consent,v.contact))['eligible']
    assert action(v,'interests',interests=['Kids Jeeps','Bicycles']).status_code==200
    assert action(v,'opt_out',reason='Customer explicitly requested no promotions by phone.').status_code==200
    assert action(v,'verify',**evidence('IMPORT_WITH_EVIDENCE')).status_code==409
    with v.sessions() as db:
        row=db.get(Consent,v.contact);assert row.status=='OPTED_OUT' and not service.eligibility(db,row)['eligible']
        assert row.interests==['Bicycles','Kids Jeeps'] and db.query(Event).count()==3
    assert not v.sends


@pytest.mark.parametrize('missing',['evidence_reference','wording','wording_version','source_description','evidence_at','affirmative','scope'])
def test_incomplete_evidence_cannot_grant(consent,missing):
    data=evidence();data.pop(missing)
    assert action(consent,'verify',**data).status_code==422
    with consent.sessions() as db:assert db.get(Consent,consent.contact).status=='NOT_REQUESTED' and not db.query(Event).count()


def test_request_requires_window_preview_and_explicit_existing_send(consent):
    v=consent;key=str(uuid4());response=prepare(v,key=key)
    assert response.status_code==200,response.text
    preview=response.json();assert preview['text']==WORDING['en'] and not v.sends
    assert prepare(v,key=key).json()==preview
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='NOT_REQUESTED'
    altered={**preview,'text':preview['text']+' Extra'}
    assert send_request(v,altered).status_code==409 and not v.sends
    assert send_request(v,preview).status_code==200
    assert send_request(v,preview).status_code==200 and len(v.sends)==1
    with v.sessions() as db:
        row=db.get(Consent,v.contact);assert row.status=='PENDING' and not service.eligibility(db,row)['eligible']
        event=db.query(Event).one();assert event.event_type=='CONSENT_REQUESTED' and event.actor_name=='quick-admin'
    assert prepare(v).status_code==409
    with v.sessions() as db:
        # Different recipient outside window must not be offered free-form requests.
        c=db.get(WhatsAppConversation,v.ids[1]);c.last_customer_message_at=datetime.utcnow()-timedelta(days=2);db.commit()
    response=v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[1]}/marketing-consent/request',json={'language':'en','key':str(uuid4())},headers={'X-CSRF-Token':v.csrf})
    assert response.status_code==409 and len(v.sends)==1


def test_yes_requires_exact_current_reply_context_and_webhook_retry_is_idempotent(consent):
    v=consent
    assert incoming(v,'YES')=='stored'
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='NOT_REQUESTED'
    preview=prepare(v).json();assert send_request(v,preview).status_code==200
    assert incoming(v,'YES')=='stored'
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='PENDING' and db.get(Consent,v.contact).needs_review
    with v.sessions() as db:req=db.get(ConsentRequest,preview['request_id']);outbound=req.whatsapp_message_id
    mid='wamid.incoming.affirmative'
    assert incoming(v,' YES ',reply=outbound,key=mid)=='stored'
    assert incoming(v,' YES ',reply=outbound,key=mid)=='duplicate'
    with v.sessions() as db:
        row=db.get(Consent,v.contact);assert row.status=='OPTED_IN' and service.eligibility(db,row)['eligible']
        assert db.query(Event).filter_by(event_type='CONSENT_GRANTED').count()==1
    assert incoming(v,'  StOp  ')=='stored'
    assert incoming(v,'YES',reply=outbound)=='stored'
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='OPTED_OUT' and not service.eligibility(db,db.get(Consent,v.contact))['eligible']
    assert len(v.sends)==1


def test_no_new_request_after_opt_out_and_phone_change_never_transfers(consent):
    v=consent;assert action(v,'verify',**evidence()).status_code==200
    with v.sessions() as db:
        c=db.get(WhatsAppConversation,v.ids[0]);old=c.phone_number;c.phone_number='94770012309';db.commit()
        row=db.get(Consent,v.contact);assert row.status=='NOT_REQUESTED' and not service.eligibility(db,row)['eligible']
        new=db.query(Consent).filter_by(phone_e164=phone(c.phone_number)).one();assert new.status=='NOT_REQUESTED'
        c.phone_number=old;db.commit()
        assert not service.eligibility(db,db.get(Consent,v.contact))['eligible']
    assert action(v,'opt_out',reason='Explicit customer opt-out.').status_code==200
    assert prepare(v).status_code==409


def test_security_csrf_scoping_verify_roles_and_immutable_audit(consent):
    v=consent
    assert v.client.post(f'/crm/marketing-consent/api/{v.contact}/action',json={}).status_code==403
    v.csrf=v.login('quick-agent')
    assert v.client.get(f'/crm/whatsapp/api/marketing-consent/{v.contact}').status_code==404
    v.csrf=v.login('quick-staff')
    assert action(v,'verify',**evidence()).status_code==403
    assert action(v,'opt_out',reason='Customer asked me to record withdrawal.').status_code==200
    history=v.client.get(f'/crm/whatsapp/api/marketing-consent/{v.contact}/history').json()
    assert history['events'][0]['status']=='OPTED_OUT' and 'evidence_reference' not in history['events'][0]
    with v.sessions() as db:
        with pytest.raises(DatabaseError):db.execute(text('DELETE FROM marketing_consent_events'))
        db.rollback()
        assert db.query(Event).count()==1 and db.get(GlobalAIReplyControl,1).enabled is False
    assert not v.sends


def test_dashboard_filters_canonical_customers_and_readonly_eligibility(consent):
    v=consent;assert action(v,'verify',**evidence()).status_code==200
    assert action(v,'interests',interests=['Toys']).status_code==200
    data=v.client.get('/crm/marketing-consent/api',params={'status':'OPTED_IN','interest':'Toys','source':'STAFF_VERIFIED_EXTERNAL'}).json()
    assert data['total']==1 and data['contacts'][0]['id']==v.contact and data['kpis']['eligible']==1
    assert v.client.get('/crm/marketing-consent/api',params={'interest':'unknown'}).status_code==422
    v.csrf=v.login('quick-agent');data=v.client.get('/crm/marketing-consent/api').json()
    assert data['total']==1 and data['kpis']['eligible']==0


def test_whatsapp_webhook_stop_is_atomic_idempotent_and_sends_no_reply(consent,monkeypatch):
    v=consent
    monkeypatch.setattr(main.settings,'META_APP_SECRET','')
    with v.sessions() as db:sender=db.get(WhatsAppConversation,v.ids[0]).phone_number
    message={'id':'wamid.consent.stop','from':sender,'type':'text','timestamp':str(int(datetime.utcnow().timestamp())),'text':{'body':' UNSUBSCRIBE '}}
    payload={'entry':[{'changes':[{'field':'messages','value':{'messages':[message]}}]}]}
    assert v.client.post('/webhook',json=payload).status_code==200
    assert v.client.post('/webhook',json=payload).status_code==200
    with v.sessions() as db:
        assert db.get(Consent,v.contact).status=='OPTED_OUT'
        assert db.query(Event).count()==1 and db.query(ProcessedWhatsAppMessage).filter_by(message_id=message['id']).count()==1
        assert db.get(GlobalAIReplyControl,1).enabled is False
    assert not v.sends


@pytest.mark.parametrize('reply',['ඔව්','ஆம்','Yes, I agree'])
def test_linked_multilingual_affirmative_and_explicit_decline(consent,reply):
    v=consent;preview=prepare(v,'si').json();assert send_request(v,preview).status_code==200
    with v.sessions() as db:outbound=db.get(ConsentRequest,preview['request_id']).whatsapp_message_id
    assert incoming(v,reply,reply=outbound)=='stored'
    with v.sessions() as db:assert service.eligibility(db,db.get(Consent,v.contact))['eligible']


def test_uncertain_submission_does_not_create_pending_or_repeat(consent,monkeypatch):
    v=consent;monkeypatch.setattr(main,'send_whatsapp_text',lambda *a,**kw:v.sends.append(a) or False)
    preview=prepare(v).json();assert send_request(v,preview).status_code==502
    assert send_request(v,preview).status_code==409 and len(v.sends)==1
    assert prepare(v).status_code==409
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='NOT_REQUESTED' and db.get(ConsentRequest,preview['request_id']).status=='UNKNOWN'


def test_optout_wins_during_request_send_and_failed_delivery_is_separate(consent,monkeypatch):
    v=consent;preview=prepare(v).json()
    def transport(*args,**kwargs):
        assert incoming(v,'STOP')=='stored';return 'wamid.request.after-stop'
    monkeypatch.setattr(main,'send_whatsapp_text',transport)
    assert send_request(v,preview).status_code==200
    with v.sessions() as db:
        row=db.get(Consent,v.contact);assert row.status=='OPTED_OUT' and not service.eligibility(db,row)['eligible']
        assert db.get(ConsentRequest,preview['request_id']).status=='SENT'
        service.delivery(db,'wamid.request.after-stop','failed');db.commit()
        assert db.get(Consent,v.contact).status=='OPTED_OUT'


def test_phone_collision_quarantines_and_append_only_correction(consent):
    v=consent
    assert action(v,'verify',**evidence()).status_code==200
    assert action(v,'correct',**{**evidence(),'evidence_reference':'secure-form:corrected'}).status_code==200
    with v.sessions() as db:
        assert db.query(Event).order_by(Event.id).first().evidence_reference=='secure-form:fixture-1'
        assert db.query(Event).order_by(Event.id.desc()).first().event_type=='CONSENT_EVIDENCE_CORRECTED'
        db.add(Customer(name='Customer one',phone='94770012301'));db.commit()
        db.add(Customer(name='Customer two',phone='0770012301'));db.commit()
        row=db.get(Consent,v.contact);db.refresh(row)
        assert row.identity_conflict and row.status=='NOT_REQUESTED' and not service.eligibility(db,row)['eligible']
    assert action(v,'verify',**evidence()).status_code==422


def test_customer_phone_change_and_recreated_customer_do_not_transfer_permission(consent):
    v=consent
    with v.sessions() as db:
        customer=Customer(name='Bound customer',phone='0770012301');db.add(customer);db.commit();customer_id=customer.id
    assert action(v,'verify',**evidence()).status_code==200
    with v.sessions() as db:
        assert service.get_marketing_eligibility(db,customer_id)['eligible']
        customer=db.get(Customer,customer_id);customer.phone='0770012309';db.commit()
        assert not service.get_marketing_eligibility(db,customer_id)['eligible']
        customer.phone='0770012301';db.commit()
        assert not service.get_marketing_eligibility(db,customer_id)['eligible']
    assert action(v,'verify',**evidence()).status_code==200
    with v.sessions() as db:
        db.delete(db.get(Customer,customer_id));db.commit()
        replacement=Customer(name='New customer after deletion',phone='0770012301');db.add(replacement);db.commit()
        assert not service.get_marketing_eligibility(db,replacement.id)['eligible']
        assert db.get(Consent,v.contact).status=='NOT_REQUESTED'


def test_expired_context_invalidates_inference_and_delivery_failure_requires_review(consent):
    v=consent;preview=prepare(v).json();assert send_request(v,preview).status_code==200
    with v.sessions() as db:
        req=db.get(ConsentRequest,preview['request_id']);outbound=req.whatsapp_message_id;req.expires_at=datetime.utcnow()-timedelta(seconds=1);db.commit()
    assert incoming(v,'YES',reply=outbound)=='stored'
    with v.sessions() as db:
        assert db.get(Consent,v.contact).status=='PENDING'
        service.delivery(db,outbound,'failed');db.commit()
        assert not service.eligibility(db,db.get(Consent,v.contact))['eligible']
        assert db.get(Consent,v.contact).needs_review


def test_stale_version_audit_failure_and_conflicting_action_keys_are_safe(consent,monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError
    v=consent
    with v.sessions() as db:version=db.get(Consent,v.contact).version
    data={'action':'interests','interests':['Toys'],'version':version,'key':str(uuid4())}
    url=f'/crm/marketing-consent/api/{v.contact}/action';headers={'X-CSRF-Token':v.csrf}
    assert v.client.post(url,json=data,headers=headers).status_code==200
    assert v.client.post(url,json=data,headers=headers).status_code==200
    assert v.client.post(url,json={**data,'interests':['Bicycles']},headers=headers).status_code==409
    assert v.client.post(url,json={**data,'key':str(uuid4())},headers=headers).status_code==409
    def fail(*args):raise SQLAlchemyError('simulated failure, never exposed')
    event.listen(Event,'before_insert',fail)
    try:assert action(v,'opt_out',reason='Recorded withdrawal.').status_code==503
    finally:event.remove(Event,'before_insert',fail)
    with v.sessions() as db:
        row=db.get(Consent,v.contact);assert row.status=='NOT_REQUESTED' and row.interests==['Toys']
        assert db.query(Event).count()==1


def test_stale_cached_eligibility_rechecks_optout_and_fresh_reconsent_requires_customer(consent):
    v=consent;assert action(v,'verify',**evidence()).status_code==200
    with v.sessions() as db:
        cached=db.get(Consent,v.contact);assert service.eligibility(db,cached)['eligible']
        assert action(v,'opt_out',reason='Customer withdrew.').status_code==200
        assert not service.eligibility(db,cached)['eligible']
    data=evidence('CUSTOMER_REQUEST');data['evidence_at']=datetime.utcnow().isoformat()+'Z'
    assert action(v,'verify',**data).status_code==200
    assert action(v,'invalidate',reason='Evidence no longer verifiable.').status_code==200
    assert action(v,'verify',**evidence('IMPORT_WITH_EVIDENCE')).status_code==409


@pytest.mark.parametrize('body',['නවත්වන්න','ප්‍රවර්ධන පණිවිඩ එවන්න එපා','விளம்பர செய்திகள் அனுப்ப வேண்டாம்','REMOVE    ME'])
def test_clear_multilingual_withdrawals_apply_without_request(consent,body):
    v=consent;assert incoming(v,body)=='stored'
    with v.sessions() as db:
        assert db.get(Consent,v.contact).status=='OPTED_OUT'
        ev=db.query(Event).one();assert ev.evidence_text==body and ev.evidence_reference.startswith('message:')


def test_identity_reconciliation_never_grants_and_import_cannot_restore_out(consent):
    v=consent
    with v.sessions() as db:
        db.add(Customer(name='First',phone='0770012301'));db.commit()
        duplicate=Customer(name='Duplicate',phone='94770012301');db.add(duplicate);db.commit();duplicate_id=duplicate.id
    assert action(v,'reconcile_identity',reason='Review identity collision.').status_code==409
    with v.sessions() as db:db.delete(db.get(Customer,duplicate_id));db.commit()
    assert action(v,'reconcile_identity',reason='Confirmed the remaining unique customer and chat.').status_code==200
    with v.sessions() as db:
        row=db.get(Consent,v.contact);assert row.status=='NOT_REQUESTED' and not row.identity_conflict and not service.eligibility(db,row)['eligible']
    assert action(v,'opt_out',reason='Explicit withdrawal.').status_code==200
    assert action(v,'reconcile_identity',reason='Reviewed canonical records only.').status_code==200
    assert action(v,'verify',**evidence('IMPORT_WITH_EVIDENCE')).status_code==409


def test_unknown_consent_without_immutable_evidence_is_ineligible(consent):
    v=consent
    with v.sessions() as db:
        row=db.get(Consent,v.contact);values=evidence();values.pop('reason');values['evidence_at']=service.timestamp(values['evidence_at'])
        service.change(db,row,row.version,{**values,'status':'OPTED_IN','consent_at':datetime.utcnow(),'last_verified_at':datetime.utcnow()});db.commit()
        assert service.eligibility(db,row)['reason']=='AUDIT_EVIDENCE_MISSING'
