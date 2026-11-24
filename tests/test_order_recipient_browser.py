import pytest
from models import WhatsAppConversation
from test_loyalty import loyalty,inbox
from test_loyalty_browser import browser

@pytest.mark.parametrize('width',[390,1280])
def test_overseas_customer_local_recipient_drawer(loyalty,width):
    from playwright.sync_api import expect
    v=loyalty
    with v.sessions() as db:
        c=db.query(WhatsAppConversation).first();c.phone_number='96550012345';c.customer_name='Overseas purchaser';cid=c.id;db.commit()
    with browser(v,width) as (page,errors,calls):
        page.goto(f'https://loyalty.test/crm/whatsapp?conversation={cid}');expect(page.locator('#chat-phone')).to_have_text('96550012345')
        if width<600:page.get_by_role('button',name='More message actions').click()
        page.locator('#create-order-button').click()
        expect(page.locator('#order-customer-country')).to_have_text('Kuwait')
        page.on('dialog',lambda dialog:dialog.accept());page.locator('#order-same-recipient').check()
        expect(page.locator('#order-delivery-phone')).to_have_value('')
        page.locator('#order-contact').fill('Local recipient');page.locator('#order-delivery-phone').fill('077 123 4567')
        expect(page.locator('#order-phone')).to_have_value('96550012345')
        expect(page.locator('#order-same-recipient')).not_to_be_checked()
        page.screenshot(path=f'/tmp/recipient-interface-{width}.png',full_page=True)
        assert not errors,errors

@pytest.mark.parametrize('width',[390,1280])
def test_manual_customer_and_recipient_create_local_only(loyalty,width,monkeypatch):
    import main
    from models import SalesOrder,JournalEntry
    from playwright.sync_api import expect
    v=loyalty
    monkeypatch.setattr(main,'search_products_for_inbox',lambda *args:{'products':[{'id':'gid://shopify/Product/1','title':'School bag','variants':[{'id':'gid://shopify/ProductVariant/1','title':'Pink','price':'4500'}]}]})
    with browser(v,width) as (page,errors,calls):
        page.goto('https://loyalty.test/crm/order-entry')
        form=page.locator('#manual-order');form.locator('[name=customer_name]').fill('Overseas buyer');form.locator('[name=whatsapp_phone]').fill('+971501234567');form.locator('[name=delivery_contact_person]').fill('Local recipient');form.locator('[name=delivery_phone]').fill('0771234567')
        page.locator('#manual-search').fill('School bag');page.locator('#manual-find').click();page.get_by_role('button',name='Add variant').click()
        page.screenshot(path=f'/tmp/recipient-manual-{width}.png',full_page=True)
        form.get_by_role('button',name='Save local draft').click();page.wait_for_url('**/crm/sales-orders/*')
        with v.sessions() as db:
            order=db.query(SalesOrder).order_by(SalesOrder.id.desc()).first();assert order.whatsapp_phone=='971501234567' and order.delivery_phone=='+94771234567' and order.customer_id is None and order.order_status=='DRAFT'
            assert db.query(JournalEntry).count()==0 and not order.shopify_draft_order_id
        assert not errors,errors
