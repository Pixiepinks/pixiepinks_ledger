"""Phase 1B uses isolated fixture databases and mocked transport only."""
import json
from uuid import uuid4
import pytest
from models import MarketingConsent as Consent,MarketingConsentEvent as Event,MarketingConsentRequest as ConsentRequest,WhatsAppConversationMessage as Message
import marketing_consent_service as service
from marketing_consent_constants import WORDING,WORDING_VERSION
from test_marketing_consent import consent,prepare,send_request,incoming,action
from test_whatsapp_quick_messages import inbox


def pending(v,reply=None,text='Yes'):
    preview=prepare(v,'si').json();assert send_request(v,preview).status_code==200
    with v.sessions() as db:outbound=db.get(ConsentRequest,preview['request_id']).whatsapp_message_id
    incoming(v,text,reply=outbound if reply=='linked' else reply)
    return preview


def detail(v):return v.client.get(f'/crm/marketing-consent/api/{v.contact}').json()


def review(v,decision='approve',**overrides):
    data=detail(v)
    body={'event_id':data['review']['event_id'],'decision':decision,'confirmed':True,'reason':'Reviewed original request and customer response.',
          'version':data['contact']['version'],'key':str(uuid4()),**overrides}
    return body,v.client.post(f'/crm/marketing-consent/api/{v.contact}/review',json=body,headers={'X-CSRF-Token':v.csrf})


@pytest.mark.parametrize('language',['si','en','ta'])
def test_versioned_wording_and_default(consent,language):
    v=consent
    if language=='si':
        response=v.client.post(f'/crm/whatsapp/api/conversations/{v.ids[0]}/marketing-consent/request',json={'key':str(uuid4())},headers={'X-CSRF-Token':v.csrf})
    else:response=prepare(v,language)
    assert response.status_code==200
    data=response.json();assert data['text']==WORDING[language] and data['version']==WORDING_VERSION+language
    assert 'Reply' in data['text'] and 'YES' in data['text'] and 'STOP' in data['text']
    assert not v.sends


@pytest.mark.parametrize('text',['YES','Yes','yes','  Yes  '])
def test_linked_variants_keep_auto_approval(consent,text):
    v=consent;pending(v,'linked',text)
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='OPTED_IN'


@pytest.mark.parametrize('reply,code',[(None,'MISSING_REPLY_CONTEXT'),('wamid.other','MISMATCHED_REPLY_CONTEXT')])
def test_diagnostic_reason_and_review_safeguards(consent,reply,code):
    v=consent;pending(v,reply)
    data=detail(v);assert data['contact']['status']=='PENDING' and data['review']['reason_code']==code
    assert data['review']['request_text']==WORDING['si'] and data['review']['response_text']=='Yes'
    assert data['review']['can_approve']==(reply is None)
    if reply is not None:assert review(v)[1].status_code==422
    filtered=v.client.get('/crm/marketing-consent/api?needs_review=true').json()
    assert filtered['total']==1 and filtered['kpis']['needs_review']==1


def test_approval_reuses_verification_and_is_idempotent(consent):
    v=consent;pending(v)
    with v.sessions() as db:
        old=[(e.id,e.reason,e.evidence_text) for e in db.query(Event).order_by(Event.id)]
    body,response=review(v);assert response.status_code==200
    again=v.client.post(f'/crm/marketing-consent/api/{v.contact}/review',json=body,headers={'X-CSRF-Token':v.csrf});assert again.status_code==200
    with v.sessions() as db:
        row=db.get(Consent,v.contact);assert row.status=='OPTED_IN' and service.eligibility(db,row)['eligible']
        assert [(db.get(Event,i).id,db.get(Event,i).reason,db.get(Event,i).evidence_text) for i,_,_ in old]==old
        assert db.query(Event).filter_by(event_type='CONSENT_VERIFIED').count()==1
    assert len(v.sends)==1


def test_incomplete_confirmation_pending_and_optout(consent):
    v=consent;pending(v)
    assert review(v,confirmed=False)[1].status_code==422
    assert review(v,'decline')[1].status_code==422
    assert review(v,'pending')[1].status_code==200
    with v.sessions() as db:assert db.query(Event).filter_by(event_type='RESPONSE_REVIEW_DEFERRED').count()==1
    incoming(v,'STOP')
    assert review(v)[1].status_code==409
    with v.sessions() as db:assert db.get(Consent,v.contact).status=='OPTED_OUT'


def test_identity_conflict_and_missing_evidence_cannot_approve(consent):
    v=consent;preview=prepare(v).json();assert send_request(v,preview).status_code==200
    with v.sessions() as db:
        row=db.get(Consent,v.contact);row.identity_conflict=True;outbound=db.get(ConsentRequest,preview['request_id']).whatsapp_message_id;db.commit()
    incoming(v,'Yes',reply=outbound)
    assert detail(v)['review']['reason_code']=='IDENTITY_CONFLICT'
    assert review(v)[1].status_code==422
    with v.sessions() as db:
        row=db.get(Consent,v.contact);row.identity_conflict=False
        msg=db.query(Message).filter_by(direction='inbound',message_text='Yes').one();msg.message_text='altered';db.commit()
    assert not detail(v)['review']['can_approve'] and review(v)[1].status_code==422


def test_historical_unknown_reason_not_inferred(consent):
    v=consent;preview=prepare(v).json();assert send_request(v,preview).status_code==200
    with v.sessions() as db:
        row=db.get(Consent,v.contact);service.change(db,row,row.version,{'needs_review':True})
        service.event(db,row,'PENDING','RESPONSE_REVIEW_REQUIRED','legacy-fixture',reason='Customer message; no automated reply was sent.',evidence={'evidence_reference':'message:999999','evidence_text':'Yes'});db.commit()
    data=detail(v)['review'];assert data['reason']=='Review required — exact reason not recorded.' and not data['can_approve']
    assert review(v)[1].status_code==422


def test_review_authorization_csrf_and_historical_wording(consent):
    v=consent;pending(v)
    with v.sessions() as db:
        request=db.query(ConsentRequest).one();original=(request.message_text,request.wording_version)
        reqid=request.id
    d=detail(v);payload={'event_id':d['review']['event_id'],'decision':'approve','confirmed':True,'reason':'Verified','version':d['contact']['version'],'key':str(uuid4())}
    assert v.client.post(f'/crm/marketing-consent/api/{v.contact}/review',json=payload).status_code==403
    # Service also enforces administrator permission independently of route scope.
    from models import User
    from fastapi import HTTPException
    with v.sessions() as db:
        user=User(id=999,username='unauthorized',role='STAFF')
        with pytest.raises(HTTPException) as error:service.review_action(db,db.get(Consent,v.contact),user,payload)
        assert error.value.status_code==403
        request=db.get(ConsentRequest,reqid);assert (request.message_text,request.wording_version)==original


def test_old_prepared_wording_is_not_rewritten(consent):
    v=consent;preview=prepare(v).json()
    legacy='Hello from PixiePinks. Receive WhatsApp promotions? Reply YES. STOP to withdraw.'
    with v.sessions() as db:
        req=db.get(ConsentRequest,preview['request_id']);req.message_text=legacy;req.wording_version='pixiepinks-wa-marketing-v1-en';db.commit()
    preview['text']=legacy;assert send_request(v,preview).status_code==200
    with v.sessions() as db:
        req=db.get(ConsentRequest,preview['request_id']);assert req.message_text==legacy and req.wording_version=='pixiepinks-wa-marketing-v1-en'
        ev=db.query(Event).filter_by(event_type='CONSENT_REQUESTED').one();assert ev.wording==legacy and ev.wording_version==req.wording_version


@pytest.mark.parametrize('missing_reference,text,expected',[(True,'Yes','MISSING_REQUEST_REFERENCE'),(False,'Maybe','OTHER_VERIFICATION_FAILURE')])
def test_remaining_precise_diagnostics(consent,missing_reference,text,expected):
    v=consent;preview=prepare(v).json();assert send_request(v,preview).status_code==200
    with v.sessions() as db:
        request=db.get(ConsentRequest,preview['request_id']);reply=request.whatsapp_message_id
        if missing_reference:request.whatsapp_message_id=None;reply=None;db.commit()
    incoming(v,text,reply=reply)
    data=detail(v);assert data['review']['reason_code']==expected
    assert not data['review']['can_approve'] and data['contact']['status']=='PENDING'


def test_decline_review_is_supported_by_actual_response(consent):
    v=consent;pending(v,text='No')
    assert detail(v)['review']['reason']=='Customer response has no WhatsApp Reply reference.'
    assert review(v,'decline')[1].status_code==200
    with v.sessions() as db:
        row=db.get(Consent,v.contact);assert row.status=='OPTED_OUT' and not service.eligibility(db,row)['eligible']
        assert db.query(Event).filter_by(event_type='CONSENT_WITHDRAWN').count()==1
