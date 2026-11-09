"""Admin routing rules, signed inbound receipts and safe optional historical assignment."""
from datetime import datetime,timedelta
import hashlib
import hmac
import json
import pytest
from sqlalchemy import event,text
from sqlalchemy.exc import SQLAlchemyError
from alembic import command
from alembic.config import Config
from pathlib import Path
import main
from models import (User,WhatsAppConversation as C,WhatsAppConversationMessage as M,WhatsAppMessageReferral as R,
    ConversationAssignmentAudit as Audit,AdAssignmentRule as Rule,AdAssignmentRuleAudit as RuleAudit,
    ProcessedWhatsAppMessage as Receipt,GlobalAIReplyControl)
from test_whatsapp_quick_messages import inbox
from test_whatsapp_referrals import payload

SECRET='ad-rule-test-secret'
AD='120000000000001'


@pytest.fixture
def routing(inbox,monkeypatch):
    monkeypatch.setattr(main.settings,'META_APP_SECRET',SECRET)
    monkeypatch.setenv('META_ADS_SYNC_ENABLED','false')
    client,sessions,ids,sends,login,csrf=inbox
    with sessions() as db:
        db.get(GlobalAIReplyControl,1).enabled=False
        users={u.username:u.id for u in db.query(User)};db.commit()
    return client,sessions,ids,sends,login,csrf,users


def put(f,aid=AD,**changes):
    client,_,_,_,_,csrf,users=f
    return client.put('/crm/ad-assignments/api/rules/'+aid,json={'staff_user_id':users['quick-staff'],
        'enabled':True,'label':'Bicycle ad','expected_version':0,**changes},headers={'X-CSRF-Token':csrf})


def incoming(f,aid=AD,mid='wamid.ad-rule',phone='94770009999',source='ad',timestamp=None,sign=True,kind='text'):
    client=f[0];body=payload(mid=mid,kind=kind);m=body['entry'][0]['changes'][0]['value']['messages'][0]
    m['from']=phone;m['referral']['source_id']=aid;m['referral']['source_type']=source
    if timestamp is not None:m['timestamp']=str(timestamp)
    raw=json.dumps(body).encode();headers={'Content-Type':'application/json'}
    if sign:headers['X-Hub-Signature-256']='sha256='+hmac.new(SECRET.encode(),raw,hashlib.sha256).hexdigest()
    return client.post('/webhook',content=raw,headers=headers)


def test_signed_ad_assigns_once_while_ai_off_without_ads_sync(routing):
    f=routing;assert put(f).status_code==200
    assert incoming(f).status_code==200 and incoming(f).status_code==200
    with f[1]() as db:
        c=db.query(C).filter_by(phone_number='94770009999').one()
        assert c.assigned_to_user_id==f[6]['quick-staff'] and c.assignment_version==1 and c.unread_count==1
        assert c.mode=='AI' and db.get(GlobalAIReplyControl,1).enabled is False
        a=db.query(Audit).one();assert a.source=='AD_RULE' and a.ad_id==AD and a.rule_version==1
        assert a.actor_id==f[6]['quick-admin'] and a.new_owner_id==f[6]['quick-staff']
        assert db.query(Receipt).filter_by(message_id='wamid.ad-rule').count()==1
    assert not f[3]
    f[4]('quick-staff');response=f[0].get('/crm/whatsapp/api/conversations?mailbox=mine')
    assert response.status_code==200 and any(row['id']==c.id for row in response.json()['conversations'])
    f[4]('quick-agent');assert f[0].get('/crm/whatsapp/api/conversations/'+str(c.id)).status_code==404


def test_existing_owner_stays_and_other_ad_never_reassigns(routing):
    f=routing;assert put(f,staff_user_id=f[6]['quick-agent']).status_code==200
    with f[1]() as db:phone=db.get(C,f[2][0]).phone_number;version=db.get(C,f[2][0]).assignment_version
    assert incoming(f,phone=phone).status_code==200
    with f[1]() as db:
        c=db.get(C,f[2][0]);assert c.assigned_to_user_id==f[6]['quick-staff'] and c.assignment_version==version
        assert db.query(Audit).count()==0
    assert incoming(f,mid='wamid.new-buyer').status_code==200
    assert put(f,aid='999',staff_user_id=f[6]['quick-staff']).status_code==200
    assert incoming(f,aid='999',mid='wamid.other-ad').status_code==200
    with f[1]() as db:assert db.query(C).filter_by(phone_number='94770009999').one().assigned_to_user_id==f[6]['quick-agent']


@pytest.mark.parametrize('source,aid',[('post',AD),('ad','999'),('ad','invalid')])
def test_nonmatching_referrals_remain_unassigned(routing,source,aid):
    f=routing;put(f);assert incoming(f,source=source,aid=aid).status_code==200
    with f[1]() as db:assert db.query(C).filter_by(phone_number='94770009999').one().assigned_to_user_id is None


def test_disabled_rule_and_unsigned_referral_do_not_assign(routing,monkeypatch):
    f=routing;put(f,enabled=False);assert incoming(f).status_code==200
    assert put(f,expected_version=1).status_code==200
    assert incoming(f,mid='wamid.bad-signature',sign=False).status_code==403
    monkeypatch.setattr(main.settings,'META_APP_SECRET',None)
    assert incoming(f,mid='wamid.unverified',sign=False).status_code==200
    with f[1]() as db:assert db.query(C).filter_by(phone_number='94770009999').one().assigned_to_user_id is None


@pytest.mark.parametrize('state',['inactive','no-access','role'])
def test_staff_eligibility_is_rechecked_for_each_inbound(routing,state):
    f=routing;put(f)
    with f[1]() as db:
        u=db.get(User,f[6]['quick-staff'])
        if state=='inactive':u.is_active=False
        elif state=='no-access':u.section_access=[]
        else:u.role='OTHER'
        db.commit()
    assert incoming(f).status_code==200
    with f[1]() as db:assert db.query(C).filter_by(phone_number='94770009999').one().assigned_to_user_id is None
    listing=f[0].get('/crm/ad-assignments/api').json();assert listing['rules'][0]['staff_eligible'] is False


def test_delayed_older_referral_does_not_override_latest_ad_evidence(routing):
    f=routing;put(f)
    assert incoming(f,aid='999',mid='wamid.newer',timestamp=200).status_code==200
    assert incoming(f,mid='wamid.older',timestamp=100).status_code==200
    with f[1]() as db:assert db.query(C).filter_by(phone_number='94770009999').one().assigned_to_user_id is None


def test_rule_versions_disable_and_configuration_history(routing):
    f=routing;assert put(f).status_code==200
    assert put(f,expected_version=1).json()['rule']['version']==1
    assert put(f,label='Another label').status_code==409
    assert put(f,expected_version=1,enabled=False).status_code==200
    assert put(f,expected_version=1,staff_user_id=f[6]['quick-agent']).status_code==409
    history=f[0].get(f'/crm/ad-assignments/api/rules/{AD}/history').json()
    assert len(history['history'])==2 and history['history'][0]['changes']['after']['enabled'] is False
    with f[1]() as db:assert db.query(RuleAudit).count()==2 and db.query(Rule).count()==1


@pytest.mark.parametrize('who',['quick-staff','quick-agent','quick-manager','quick-denied'])
def test_admin_only_controls_and_csrf(routing,who):
    f=routing;assert f[0].put(f'/crm/ad-assignments/api/rules/{AD}',json={}).status_code==403
    f[4](who)
    for path in ['/crm/ad-assignments','/crm/ad-assignments/api',f'/crm/ad-assignments/api/rules/{AD}/history',f'/crm/ad-assignments/api/rules/{AD}/preview']:
        assert f[0].get(path).status_code==403
    assert put(f).status_code==403
    assert f[0].post(f'/crm/ad-assignments/api/rules/{AD}/apply-existing',json={}).status_code==403


@pytest.mark.parametrize('changes',[{'staff_user_id':True},{'staff_user_id':999999},{'enabled':'true'},{'label':'x'*121},{'expected_version':True}])
def test_invalid_rule_values(routing,changes):assert put(routing,**changes).status_code==422


def history_chat(db,phone,aid=AD,owner=None):
    c=C(phone_number=phone,customer_name='Buyer '+phone[-2:],mode='HUMAN',assigned_to_user_id=owner);db.add(c);db.flush()
    m=M(phone_number=phone,direction='inbound',message_text='Hello',whatsapp_message_id='old-'+phone,created_at=datetime.utcnow()-timedelta(days=1));db.add(m);db.flush()
    db.add(R(message_id=m.id,source_type='ad',source_id=aid));db.flush();return c


def apply(f,p):return f[0].post(f'/crm/ad-assignments/api/rules/{AD}/apply-existing',json={'expected_version':p['version'],'preview_token':p['preview_token']},headers={'X-CSRF-Token':f[5]})


def test_existing_preview_preserves_owners_and_rejects_stale_confirmation(routing):
    f=routing;put(f)
    with f[1]() as db:
        a=history_chat(db,'94771111111');b=history_chat(db,'94772222222');owned=history_chat(db,'94773333333',owner=f[6]['quick-agent'])
        other=history_chat(db,'94774444444');m=M(phone_number=other.phone_number,direction='inbound',message_text='new ad',created_at=datetime.utcnow());db.add(m);db.flush();db.add(R(message_id=m.id,source_type='ad',source_id='999'))
        ids=[a.id,b.id,owned.id,other.id];db.commit()
    url=f'/crm/ad-assignments/api/rules/{AD}/preview';p=f[0].get(url).json();assert p['total']==2
    with f[1]() as db:db.get(C,ids[0]).assigned_to_user_id=f[6]['quick-agent'];db.get(C,ids[0]).assignment_version+=1;db.commit()
    assert apply(f,p).status_code==409
    p=f[0].get(url).json();result=apply(f,p);assert result.status_code==200 and result.json()['assigned']==1
    with f[1]() as db:
        assert db.get(C,ids[0]).assigned_to_user_id==f[6]['quick-agent']
        assert db.get(C,ids[1]).assigned_to_user_id==f[6]['quick-staff']
        assert db.get(C,ids[2]).assigned_to_user_id==f[6]['quick-agent'] and db.get(C,ids[3]).assigned_to_user_id is None
        assert db.query(Audit).one().source=='AD_RULE_EXISTING'
    assert not f[3]


def test_webhook_audit_failure_rolls_back_receipt_and_can_retry(routing):
    f=routing;put(f)
    def fail(*args):raise SQLAlchemyError('private failure details')
    event.listen(Audit,'before_insert',fail)
    try:response=incoming(f)
    finally:event.remove(Audit,'before_insert',fail)
    assert response.status_code==503 and 'private failure' not in response.text
    with f[1]() as db:
        assert db.query(Receipt).filter_by(message_id='wamid.ad-rule').count()==0
        assert db.query(C).filter_by(phone_number='94770009999').count()==0
    assert incoming(f).status_code==200
    with f[1]() as db:assert db.query(Audit).count()==1


def test_migration_preserves_legacy_owner_and_audit(routing):
    f=routing
    with f[1]() as db:
        db.add(Audit(conversation_id=f[2][0],actor_id=f[6]['quick-admin'],new_owner_id=f[6]['quick-staff']));db.commit()
        RuleAudit.__table__.drop(db.get_bind());Rule.__table__.drop(db.get_bind())
        db.execute(text('DROP INDEX ix_conversation_assignment_audits_rule_id'))
        for column in ['source','ad_id','rule_id','rule_version']:db.execute(text('ALTER TABLE conversation_assignment_audits DROP COLUMN '+column))
        db.commit()
    cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'));command.stamp(cfg,'20261109_36');command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with f[1]() as db:
        assert db.get(C,f[2][0]).assigned_to_user_id==f[6]['quick-staff']
        assert db.query(Audit).one().source=='MANUAL' and db.query(Rule).count()==0


def test_existing_assignment_is_bounded_and_repeated_batches_are_safe(routing):
    f=routing;put(f)
    with f[1]() as db:
        for i in range(201):history_chat(db,f'9477800{i:04d}')
        db.commit()
    url=f'/crm/ad-assignments/api/rules/{AD}/preview';p=f[0].get(url).json()
    assert p['total']==201 and p['batch_size']==200
    result=apply(f,p);assert result.status_code==200 and result.json()=={'assigned':200,'remaining':1}
    assert apply(f,p).status_code==409
    result=apply(f,f[0].get(url).json());assert result.json()=={'assigned':1,'remaining':0}
    with f[1]() as db:assert db.query(Audit).count()==201


def test_concurrent_automatic_assignment_audits_once(routing):
    from concurrent.futures import ThreadPoolExecutor
    import ad_assignment_service as service
    f=routing;put(f)
    with f[1]() as db:
        c=history_chat(db,'94775555555');db.commit();cid=c.id
    def attempt(_):
        with f[1]() as db:
            c=db.get(C,cid);m=db.query(M).filter_by(phone_number=c.phone_number).one()
            result=service.inbound(db,c,m,{'source_id':AD,'source_type':'ad'},verified=True);db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,[1,2]))
    assert sorted(results)==[False,True]
    with f[1]() as db:assert db.get(C,cid).assignment_version==1 and db.query(Audit).count()==1


def test_save_does_not_backfill_and_changed_rule_requires_fresh_preview(routing):
    f=routing
    with f[1]() as db:c=history_chat(db,'94778888888');db.commit();cid=c.id
    put(f)
    with f[1]() as db:assert db.get(C,cid).assigned_to_user_id is None and db.query(Audit).count()==0
    url=f'/crm/ad-assignments/api/rules/{AD}/preview';p=f[0].get(url).json()
    assert put(f,expected_version=1,staff_user_id=f[6]['quick-agent']).status_code==200
    assert apply(f,p).status_code==409
    assert apply(f,f[0].get(url).json()).json()['assigned']==1
    with f[1]() as db:assert db.get(C,cid).assigned_to_user_id==f[6]['quick-agent']


@pytest.mark.parametrize('kind',['image','audio'])
def test_media_referral_routes_without_sending_a_message(routing,kind):
    f=routing;put(f);assert incoming(f,kind=kind).status_code==200
    with f[1]() as db:assert db.query(C).filter_by(phone_number='94770009999').one().assigned_to_user_id==f[6]['quick-staff']
    assert not f[3]
