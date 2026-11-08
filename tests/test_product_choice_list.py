"""Numbered captions and sent-product-bound choice lists; no real IO."""
from datetime import datetime,timedelta
import pytest
from models import CustomerQuestionProductSend as Item,WhatsAppConversationMessage as Message,GlobalAIReplyControl
from test_question_products import product_flow,questions,inbox,flow,start
from test_customer_questions import reply,act,settings
import customer_questions as q
import question_product_service as products


@pytest.fixture(autouse=True)
def choice_transport(questions,monkeypatch):
    calls=questions[-1]
    monkeypatch.setattr(q,'send_buttons',lambda phone,body,buttons,**kw:calls.append((phone,body,buttons)) or f'wamid.choice.{len(calls)}')


def choosing(count=3):
    definition=flow(count);definition['steps'][0].update(choose=True,choice_body='Which bicycle do you like?',choice_button='Choose bicycle');return definition


def finish_cards(v,count):
    for _ in range(count):assert q.dispatch_one(v[1])
    assert q.dispatch_one(v[1])


def test_numbered_cards_matching_list_and_exact_selection(product_flow):
    v,calls=product_flow;start(v,choosing(10));finish_cards(v,10)
    assert calls[0][1].startswith('1. Bicycle 1') and calls[-1][1].startswith('10. Bicycle 10')
    assert len(v[-1])==1 and [row['title'] for row in v[-1][0][2]]==[f'{i}. Bicycle {i}' for i in range(1,11)]
    assert not q.dispatch_one(v[1])
    # Button-format or wrong-context responses cannot select a product.
    assert reply(v,index=6).status_code==200
    assert reply(v,index=6,reply_type='list_reply',context=False).status_code==200
    with v[1]() as db:assert q.latest(db,v[2][0]).status=='WAITING'
    assert reply(v,index=6,reply_type='list_reply',mid='wamid.product.choice').status_code==200
    assert reply(v,index=6,reply_type='list_reply',mid='wamid.product.choice').status_code==200
    with v[1]() as db:
        run=q.latest(db,v[2][0]);assert run.status=='COMPLETE'
        assert run.answers['recommendations']=={'product_id':'gid://shopify/Product/7','variant_id':'gid://shopify/ProductVariant/701','number':7,'title':'Bicycle 7','variant_title':'Red'}
        from models import CustomerQuestionEvent as Event
        assert db.query(Event).filter_by(action='PRODUCT_SELECTED').count()==1
        assert db.get(GlobalAIReplyControl,1).enabled is False
    data=v[0].get(f'/crm/whatsapp/api/conversations/{v[2][0]}').json()
    assert any(len(row.get('interactive_buttons',[]))==10 for row in data['messages']) and 'ppq:' not in str(data)
    progress=v[0].get(f'/crm/whatsapp/api/conversations/{v[2][0]}/customer-questions').json()['run']['products']
    assert progress['sent']==10 and progress['total']==10


def test_skips_keep_numbering_gaps_and_cannot_be_selected(product_flow,monkeypatch):
    from test_shopify_collection_sends import product
    v,calls=product_flow;start(v,choosing(3))
    monkeypatch.setattr(products,'resolve_inbox_product',lambda pid,vid:None if pid.endswith('/2') else product(int(pid.rsplit('/',1)[-1])))
    finish_cards(v,3);assert [row['title'] for row in v[-1][0][2]]==['1. Bicycle 1','3. Bicycle 3']
    assert calls[1][1].startswith('3. Bicycle 3')
    assert reply(v,index=1,reply_type='list_reply').status_code==200
    with v[1]() as db:assert q.latest(db,v[2][0]).status=='WAITING'
    assert reply(v,index=2,reply_type='list_reply').status_code==200
    with v[1]() as db:assert q.latest(db,v[2][0]).answers['recommendations']['number']==3


def test_failed_list_never_replays_cards_or_invitation(product_flow,monkeypatch):
    v,calls=product_flow;start(v,choosing(2))
    assert q.dispatch_one(v[1]) and q.dispatch_one(v[1])
    sent=[]
    monkeypatch.setattr(q,'send_buttons',lambda *a,**kw:sent.append(1) or None)
    assert q.dispatch_one(v[1]) and not q.dispatch_one(v[1])
    assert len(calls)==2 and len(sent)==1
    with v[1]() as db:
        assert q.latest(db,v[2][0]).status=='UNCERTAIN'
        assert db.query(Item).filter_by(position=-1).one().status=='UNCERTAIN'


def test_pause_before_invitation_resumes_without_resending_cards(product_flow):
    v,calls=product_flow;start(v,choosing(1));assert q.dispatch_one(v[1])
    with v[1]() as db:version=q.latest(db,v[2][0]).version
    assert act(v[0],v[2][0],v[5],'pause',version).status_code==200 and not q.dispatch_one(v[1])
    with v[1]() as db:version=q.latest(db,v[2][0]).version
    assert act(v[0],v[2][0],v[5],'resume',version).status_code==200
    assert q.dispatch_one(v[1]) and len(calls)==1 and len(v[-1])==1


def test_all_unavailable_finishes_without_empty_list(product_flow,monkeypatch):
    v,calls=product_flow;start(v,choosing(2));monkeypatch.setattr(products,'resolve_inbox_product',lambda *a:None)
    finish_cards(v,2)
    assert not calls and not v[-1] and not q.dispatch_one(v[1])
    with v[1]() as db:assert q.latest(db,v[2][0]).status=='COMPLETE'


def test_choice_uses_claim_time_when_provider_ack_is_delayed(product_flow):
    v,_=product_flow;start(v,choosing(1));finish_cards(v,1)
    with v[1]() as db:
        run=q.latest(db,v[2][0]);run.updated_at=datetime.utcnow()+timedelta(seconds=3);db.commit()
    assert reply(v,index=0,reply_type='list_reply').status_code==200
    with v[1]() as db:assert q.latest(db,v[2][0]).status=='COMPLETE'


@pytest.mark.parametrize('changes',[{'choose':True,'products':flow(11)['steps'][0]['products']}, {'choose':'yes'}, {'choose':True,'choice_button':'x'*21}, {'choose':True,'choice_body':''}])
def test_product_choice_validation(questions,changes):
    definition=flow(1);definition['steps'][0].update(changes)
    assert settings(questions[0],questions[5],definition=definition).status_code==422


def test_list_crash_preserves_claim_without_replaying_cards(product_flow,monkeypatch):
    v,calls=product_flow;start(v,choosing(1));assert q.dispatch_one(v[1])
    def crash(*a,**kw):raise KeyboardInterrupt('fixture crash after provider boundary')
    monkeypatch.setattr(q,'send_buttons',crash)
    with pytest.raises(KeyboardInterrupt):q.dispatch_one(v[1])
    assert not q.dispatch_one(v[1]) and len(calls)==1
    with v[1]() as db:
        assert db.query(Item).filter_by(position=-1).one().status=='SENDING'
        assert q.latest(db,v[2][0]).status=='SENDING'


def test_long_names_are_numbered_and_choice_keeps_original_sent_title(product_flow,monkeypatch):
    from test_shopify_collection_sends import product
    v,calls=product_flow;title='A very long bicycle product name with many words'
    start(v,choosing(2))
    monkeypatch.setattr(products,'resolve_inbox_product',lambda pid,vid:product(int(pid.rsplit('/',1)[-1]),title=title))
    finish_cards(v,2)
    assert [row['title'] for row in v[-1][0][2]]==[(f'{i}. '+title)[:24] for i in [1,2]]
    assert reply(v,index=1,reply_type='list_reply').status_code==200
    with v[1]() as db:
        value=q.latest(db,v[2][0]).answers['recommendations']
        assert value['title']==title and value['product_id'].endswith('/2')
