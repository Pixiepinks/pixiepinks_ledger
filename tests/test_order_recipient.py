from types import SimpleNamespace
import pytest
from order_recipient import local_mobile,values,shipping,customer_country
from test_shopify_draft_order_sync import order,available,setup_function,FakeShopify
from database import SessionLocal
import shopify_draft_order_service as sync
import sales_order_service as service

@pytest.mark.parametrize('raw',['0771234567','94771234567','+94771234567','077 123-4567'])
def test_local_formats(raw):assert local_mobile(raw)=='+94771234567'
@pytest.mark.parametrize('raw',['+96512345678','0112345678','bad','123','+971501234567'])
def test_invalid_local(raw):
    with pytest.raises(ValueError):local_mobile(raw)
@pytest.mark.parametrize('phone,country',[('+96550012345','KW'),('+971501234567','AE'),('+94771234567','LK'),('+447911123456','GG')])
def test_customer_countries(phone,country):assert customer_country(phone)==country
@pytest.mark.parametrize('phone',['+96550012345','+971501234567','+94773322111'])
def test_buyer_and_recipient_mapping_independent(phone,monkeypatch):
    monkeypatch.setattr(sync,'resolve_inbox_product',available)
    with SessionLocal() as db:
        o=order(db);o.whatsapp_phone=phone;o.delivery_phone='+94771234567';o.delivery_contact_person='Gift recipient';o.delivery_alternative_phone='+94773322111';o.delivery_notes='Leave at reception'
        result=sync._shopify_input(o)
        assert result['phone']==phone and result['shippingAddress']['phone']=='+94771234567'
        assert result['shippingAddress']['firstName']=='Gift recipient' and 'customerId' not in result
        assert o.whatsapp_phone==phone
@pytest.mark.parametrize('method',['PICKUP','IN_STORE'])
def test_non_delivery(method):assert shipping(SimpleNamespace(delivery_method=method)) is None

def test_missing_primary_rejects_overseas_buyer(monkeypatch):
    monkeypatch.setattr(sync,'resolve_inbox_product',available)
    with SessionLocal() as db:
        o=order(db);o.whatsapp_phone='+96550012345';o.delivery_phone=None
        with pytest.raises(sync.ShopifyDraftSyncError,match='Local delivery contact required'):sync._validate_local_order(o)
        o.delivery_phone='+94771234567';sync._validate_local_order(o)
        assert o.whatsapp_phone=='+96550012345'

@pytest.mark.parametrize('value',[None,''])
def test_optional_alternative(value):assert values({'delivery_alternative_phone':value})['delivery_alternative_phone'] is None

def test_no_overseas_legacy_copy():assert 'delivery_phone' not in values({'customer_name':'Buyer','delivery_contact_person':'Buyer','whatsapp_phone':'+96550012345'})
def test_no_other_recipient_legacy_copy():assert 'delivery_phone' not in values({'customer_name':'Buyer','delivery_contact_person':'Gift','whatsapp_phone':'0771234567'})

def test_missing_contact_then_corrected_retry_is_one_draft(monkeypatch):
    monkeypatch.setattr(sync,'resolve_inbox_product',available)
    with SessionLocal() as db:
        o=order(db);o.whatsapp_phone='96550012345';o.delivery_phone=None;db.commit();client=FakeShopify()
        with pytest.raises(sync.ShopifyDraftSyncError):sync.create_shopify_draft(db,o,'sync-admin',client)
        assert not client.calls
        o.delivery_phone='+94771234567';db.commit()
        sync.create_shopify_draft(db,o,'sync-admin',client)
        sync.create_shopify_draft(db,o,'sync-admin',client)
        assert sum('CreateLedgerDraft' in q for q,v in client.calls)==1

def test_verified_buyer_binding_never_uses_recipient_identity(monkeypatch):
    from models import Customer,User
    from pos_models import POSCustomerBinding
    monkeypatch.setattr(sync,'resolve_inbox_product',available);monkeypatch.setattr(sync.settings,'SHOPIFY_SHOP','recipient-shop')
    with SessionLocal() as db:
        buyer=Customer(name='Purchaser binding test',phone='+96550012345');db.add(buyer);db.flush()
        user=db.query(User).filter_by(username='sync-admin').one()
        db.add(POSCustomerBinding(shop='recipient-shop',customer_id=buyer.id,shopify_customer_id='gid://shopify/Customer/123',actor_id=user.id));db.commit()
        o=order(db);o.customer_id=buyer.id;o.whatsapp_phone='96550012345';o.delivery_contact_person='Recipient not a customer'
        result=sync._shopify_input(o)
        assert result['purchasingEntity']=={'customerId':'gid://shopify/Customer/123'}
        assert db.query(Customer).filter_by(name='Recipient not a customer').count()==0
        assert buyer.phone=='+96550012345'

def test_recipient_documents_do_not_use_buyer_phone(monkeypatch):
    from models import SalesDeliveryDetail
    from sales_fulfillment_service import create_full_fulfillment
    with SessionLocal() as db:
        o=order(db);o.order_status='CONFIRMED';o.whatsapp_phone='96550012345';o.delivery_contact_person='Local recipient';o.delivery_phone='+94771234567';db.commit()
        f=create_full_fulfillment(db,o.id,'sync-admin','recipient-document-test')
        detail=db.query(SalesDeliveryDetail).filter_by(fulfillment_id=f.id).one()
        assert detail.contact_snapshot=='Local recipient' and '+94771234567' in detail.address_snapshot and '96550012345' not in detail.address_snapshot
