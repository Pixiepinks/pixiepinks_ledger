"""Product steps use live metadata and durable per-card claims. All IO mocked."""
from decimal import Decimal
from datetime import datetime,timedelta
import pytest
from fastapi import HTTPException
from models import CustomerQuestionProductSend as Item,WhatsAppConversationMessage as Message,WhatsAppConversation as C,GlobalAIReplyControl
import customer_questions as q
import question_product_service as products
from test_customer_questions import questions,inbox,settings,act,reply
from test_shopify_collection_sends import product


def selection(number):
    return {'product_id':f'gid://shopify/Product/{number}','variant_id':f'gid://shopify/ProductVariant/{number}01','label':f'Selected product {number}'}


def flow(count=2,branch=False):
    step={'key':'recommendations','type':'products','products':[selection(i) for i in range(1,count+1)]}
    return {'title':'Selected products','steps':([{'key':'age','body':'Age?','options':['Seven','Eight'],'next':['recommendations','recommendations']}] if branch else [])+[step]}


@pytest.fixture
def product_flow(questions,monkeypatch):
    calls=[]
    monkeypatch.setattr(products,'resolve_inbox_product',lambda pid,vid:product(int(pid.rsplit('/',1)[-1])))
    monkeypatch.setattr(products,'send_whatsapp_image',lambda phone,image,body,**kw:calls.append((image,body)) or ('SENT',f'wamid.product.{len(calls)}'))
    return questions,calls


def start(v,definition):
    assert settings(v[0],v[5],definition=definition,enabled=True).status_code==200
    response=act(v[0],v[2][0],v[5]);assert response.status_code==200,response.text


def test_twenty_selected_cards_order_metadata_and_no_replay(product_flow):
    v,calls=product_flow;start(v,flow(20))
    for i in range(20):assert q.dispatch_one(v[1]) and len(calls)==i+1
    assert not q.dispatch_one(v[1])
    with v[1]() as db:
        run=q.latest(db,v[2][0]);assert run.status=='COMPLETE'
        assert db.query(Item).filter_by(status='SENT').count()==20
        rows=db.query(Message).filter_by(response_kind='question_product').order_by(Message.id).all()
        assert len(rows)==20 and rows[0].product_title=='Bicycle 1' and rows[-1].shopify_product_reference.endswith('/20')
        assert all(row.analytics_actor_kind=='automatic' and row.sent_by is None for row in rows)
        assert db.get(GlobalAIReplyControl,1).enabled is False
    response=v[0].get(f'/crm/whatsapp/api/conversations/{v[2][0]}/customer-questions').json()
    assert response['run']['products']['sent']==20 and response['run']['products']['total']==20
    assert 'Price: Rs. 45,000.00' in calls[0][1] and 'View Product: https://www.pixiepinks.shop/products/bicycle-1' in calls[0][1]


def test_branch_to_products_and_live_prices_not_cached_labels(product_flow,monkeypatch):
    v,calls=product_flow;start(v,flow(branch=True));assert q.dispatch_one(v[1])
    assert reply(v,index=0,mid='wamid.select.products').status_code==200
    assert reply(v,index=0,mid='wamid.select.products').status_code==200
    monkeypatch.setattr(products,'resolve_inbox_product',lambda *a:product(1,title='Live gift pack',price=Decimal('1900.00')))
    assert q.dispatch_one(v[1]) and len(calls)==1
    assert 'Live gift pack' in calls[0][1] and '1,900.00' in calls[0][1]
    assert 'Selected product' not in calls[0][1]


@pytest.mark.parametrize('outcome',['UNKNOWN','FAILED'])
def test_partial_failure_stops_remaining_and_never_retries(product_flow,monkeypatch,outcome):
    v,calls=product_flow;start(v,flow(3));assert q.dispatch_one(v[1])
    attempts=[]
    monkeypatch.setattr(products,'send_whatsapp_image',lambda *a,**kw:attempts.append(1) or (outcome,None))
    assert q.dispatch_one(v[1]);assert not q.dispatch_one(v[1]);assert len(calls)==1 and len(attempts)==1
    with v[1]() as db:
        assert q.latest(db,v[2][0]).status=='UNCERTAIN' and db.query(Item).count()==2
        assert db.query(Item).filter_by(status='SENT').count()==1


def test_unavailable_skips_and_window_rechecked_per_card(product_flow,monkeypatch):
    v,calls=product_flow;start(v,flow(3));monkeypatch.setattr(products,'resolve_inbox_product',lambda *a:None)
    assert q.dispatch_one(v[1]) and not calls
    with v[1]() as db:
        assert db.query(Item).one().status=='SKIPPED'
        db.get(C,v[2][0]).last_customer_message_at=datetime.utcnow()-timedelta(hours=25);db.commit()
    assert q.dispatch_one(v[1]) and not calls
    with v[1]() as db:assert q.latest(db,v[2][0]).status=='WINDOW_CLOSED'


def test_pause_resume_between_cards_preserves_sent_positions(product_flow):
    v,calls=product_flow;start(v,flow(2));assert q.dispatch_one(v[1])
    with v[1]() as db:version=q.latest(db,v[2][0]).version
    assert act(v[0],v[2][0],v[5],'pause',version).status_code==200
    assert not q.dispatch_one(v[1])
    with v[1]() as db:version=q.latest(db,v[2][0]).version
    assert act(v[0],v[2][0],v[5],'resume',version).status_code==200
    assert q.dispatch_one(v[1]) and len(calls)==2 and not q.dispatch_one(v[1])


def test_lost_post_send_commit_is_never_replayed(product_flow,monkeypatch):
    v,calls=product_flow;start(v,flow(2))
    def send(*a,**kw):
        calls.append('accepted')
        # Simulate process failure after provider acceptance, before local commit.
        raise KeyboardInterrupt('fixture crash')
    monkeypatch.setattr(products,'send_whatsapp_image',send)
    with pytest.raises(KeyboardInterrupt):q.dispatch_one(v[1])
    assert not q.dispatch_one(v[1]) and calls==['accepted']
    with v[1]() as db:
        assert q.latest(db,v[2][0]).status=='SENDING' and db.query(Item).one().status=='SENDING'


@pytest.mark.parametrize('definition',[flow(0),flow(21),{'title':'Duplicates','steps':[{'key':'products','type':'products','products':[selection(1),selection(1)]}]},
    {'title':'Unsafe','steps':[{'key':'products','type':'products','products':[{'product_id':'https://evil.invalid','variant_id':'1','label':'Bad'}]}]}])
def test_selection_validation(questions,definition):
    assert settings(questions[0],questions[5],definition=definition).status_code==422


def test_fifty_bounded_steps_and_request_size_limit(questions):
    definition={'title':'Age and product branches','steps':[{'key':f'question_{i}','body':'Age?','options':['Continue']} for i in range(49)]+[flow(1)['steps'][0]]}
    assert q.validate(definition)==definition
    with pytest.raises(HTTPException):q.validate({'title':'Too many','steps':definition['steps']+[{'key':'extra','body':'x','options':['x']}]})
    response=questions[0].post('/crm/quick-messages/customer-questions/api',content=b'x'*262145,headers={'X-CSRF-Token':questions[5],'Content-Type':'application/json'})
    assert response.status_code==413


def test_real_image_transport_payload_with_global_ai_off(product_flow,monkeypatch):
    import httpx,whatsapp_service
    from settings import settings as env
    v,_=product_flow;start(v,flow(1));captured=[]
    monkeypatch.setattr(env,'META_WHATSAPP_ACCESS_TOKEN','fixture-token')
    monkeypatch.setattr(env,'META_WHATSAPP_PHONE_NUMBER_ID','fixture-phone')
    monkeypatch.setattr(products,'send_whatsapp_image',whatsapp_service.send_whatsapp_image)
    def post(url,**kw):
        captured.append(kw['json']);return httpx.Response(200,json={'messages':[{'id':'wamid.product.payload'}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',post)
    assert q.dispatch_one(v[1])
    assert captured[0]['type']=='image' and captured[0]['image']['link']=='https://cdn.shopify.com/bicycle.png'
    assert 'Price: Rs. 45,000.00' in captured[0]['image']['caption']
    with v[1]() as db:assert db.get(GlobalAIReplyControl,1).enabled is False
    data=v[0].get(f'/crm/whatsapp/api/conversations/{v[2][0]}').json()
    card=next(row for row in data['messages'] if row['source']=='question_product')
    assert card['product']['title']=='Bicycle 1'


def test_catalogue_failure_and_mismatched_variant_do_not_send(product_flow,monkeypatch):
    from shopify_catalog_service import ShopifyCatalogError
    v,calls=product_flow;start(v,flow(2))
    monkeypatch.setattr(products,'resolve_inbox_product',lambda *a:product(99))
    assert q.dispatch_one(v[1]) and not calls
    def fail(*a):raise ShopifyCatalogError('fixture')
    monkeypatch.setattr(products,'resolve_inbox_product',fail)
    assert q.dispatch_one(v[1]) and not q.dispatch_one(v[1]) and not calls
    with v[1]() as db:
        assert q.latest(db,v[2][0]).status=='UNCERTAIN'
        assert db.query(Item).filter_by(status='FAILED').count()==1


def test_global_off_between_claim_and_send_can_resume_without_losing_item(product_flow):
    from contextlib import contextmanager
    v,calls=product_flow;start(v,flow(1));sessions=v[1];opened=0
    @contextmanager
    def interrupted():
        nonlocal opened
        opened+=1
        if opened==2:
            with sessions() as db:q.config(db).enabled=False;db.commit()
        with sessions() as db:yield db
    assert q.dispatch_one(interrupted) and not calls
    with sessions() as db:
        run=q.latest(db,v[2][0]);assert run.status=='PAUSED' and db.query(Item).one().status=='QUEUED'
        version=run.version;q.config(db).enabled=True;db.commit()
    assert act(v[0],v[2][0],v[5],'resume',version).status_code==200
    assert q.dispatch_one(sessions) and len(calls)==1
    with sessions() as db:assert db.query(Item).count()==1 and db.query(Item).one().status=='SENT'
