"""Production-shaped registration failures; no live customers or requests."""
from pathlib import Path
import pytest
from models import Customer,WhatsAppConversation,SalesOrder,JournalEntry
from loyalty_models import LoyaltyMember
from test_loyalty import loyalty,inbox
from test_loyalty_browser import browser


def open_order(page,v,number='94779876543',width=1280):
    from playwright.sync_api import expect
    with v.sessions() as db:
        c=db.query(WhatsAppConversation).first();c.phone_number=number;c.customer_name='Synthetic purchaser';cid=c.id;db.commit()
    page.goto('https://loyalty.test/crm/whatsapp?conversation='+str(cid))
    expect(page.locator('#chat-phone')).to_have_text(number)
    if width<600:page.get_by_role('button',name='More message actions').click()
    page.locator('#create-order-button').click()


@pytest.mark.parametrize('failure',['network','403','401','500','invalid-json','redirect'])
def test_lookup_failure_never_means_new_customer_and_retry_recovers(loyalty,failure):
    from playwright.sync_api import expect
    with browser(loyalty) as (page,errors,calls):
        def fail(route):
            if failure=='network':route.abort('failed')
            elif failure=='redirect':route.fulfill(status=302,headers={'location':'/login'})
            elif failure=='invalid-json':route.fulfill(status=200,content_type='text/html',body='not JSON')
            else:route.fulfill(status=int(failure),content_type='application/json',body='{"detail":"Access or service unavailable"}')
        pattern='**/api/customer-registration/search?**';page.route(pattern,fail)
        open_order(page,loyalty)
        expect(page.locator('#order-lookup-status')).to_have_attribute('data-state','error')
        expect(page.locator('#order-register-customer')).not_to_be_visible()
        expect(page.locator('#order-lookup-retry')).to_be_visible()
        if failure=='network':expect(page.locator('#order-lookup-status')).to_contain_text('network failure')
        elif failure not in {'invalid-json','redirect'}:expect(page.locator('#order-lookup-status')).to_contain_text('HTTP '+failure)
        with loyalty.sessions() as db:assert db.query(Customer).count()==1
        page.unroute(pattern,fail);page.locator('#order-lookup-retry').click()
        expect(page.locator('#order-lookup-status')).to_have_attribute('data-state','empty')
        expect(page.locator('#order-register-customer')).to_be_visible()
        expect(page.locator('#order-register-customer')).to_be_disabled()
        expect(page.locator('#order-customer-country')).to_contain_text('Sri Lanka')
        assert not errors


@pytest.mark.parametrize('width',[390,1280])
def test_compact_registration_country_confirmation_and_recipient_separation(loyalty,width):
    from playwright.sync_api import expect
    with browser(loyalty,width) as (page,errors,calls):
        open_order(page,loyalty,number='96550012345',width=width)
        expect(page.locator('#order-lookup-status')).to_have_attribute('data-state','empty')
        expect(page.locator('#order-phone')).to_have_value('+96550012345')
        expect(page.locator('#order-customer-country')).to_contain_text('Kuwait')
        assert not page.locator('#order-advanced-identity').evaluate('(e)=>e.open')
        assert page.locator('#order-phone').bounding_box()['y']<page.locator('#order-loyalty').bounding_box()['y']
        page.locator('#order-identity-confirm').check()
        assert page.locator('#order-identity-confirm').bounding_box()['height']<=20
        page.locator('.order-drawer-body').evaluate('(e)=>e.scrollTop=0')
        page.screenshot(path=f'/tmp/registration-fix-ready-{width}.png',full_page=True)
        page.locator('#order-register-customer').click()
        expect(page.locator('#order-registration-feedback')).to_contain_text('Customer registered and selected')
        page.locator('#order-delivery-phone').fill('0771111111')
        expect(page.locator('#order-phone')).to_have_value('+96550012345')
        with loyalty.sessions() as db:
            c=db.query(Customer).filter_by(name='Synthetic purchaser').one();assert c.phone=='+96550012345'
            assert db.query(SalesOrder).count()==0 and db.query(JournalEntry).count()==0 and db.query(LoyaltyMember).count()==0
        page.locator('.order-drawer-body').evaluate('(e)=>e.scrollTop=0')
        page.screenshot(path=f'/tmp/registration-fix-after-{width}.png',full_page=True)
        assert page.locator('#save-order').is_visible() and not errors
        assert not any(path=='/api/sales-orders/identity/country' for _,path in calls)


def test_existing_matches_and_ambiguous_phone_require_explicit_selection(loyalty):
    from playwright.sync_api import expect
    with loyalty.sessions() as db:db.add(Customer(name='Separate shared identity',phone='+94771234567'));db.commit()
    with browser(loyalty) as (page,errors,calls):
        open_order(page,loyalty,number='94771234567')
        expect(page.locator('#order-lookup-status')).to_have_attribute('data-state','ambiguous')
        expect(page.locator('#order-loyalty-customer')).to_have_value('')
        expect(page.locator('#order-register-customer')).not_to_be_visible()
        page.locator('#order-loyalty-customer').select_option(str(loyalty.cid))
        page.locator('#order-select-customer').click()
        expect(page.locator('#order-registration-feedback')).to_contain_text('Customer selected')
        assert not errors


def test_lookup_response_includes_normalized_country_and_preserves_access(loyalty):
    v=loyalty
    r=v.client.get('/api/customer-registration/search?channel=CRM&q=94771234567')
    assert r.status_code==200 and r.json()['normalized_mobile']=='+94771234567' and r.json()['country']=='LK'
    assert r.headers['cache-control']=='private, no-store'
    v.login('quick-denied')
    assert v.client.get('/api/customer-registration/search?channel=CRM&q=94771234567').status_code==403


def test_stale_lookup_cannot_replace_new_purchaser_or_country(loyalty):
    from playwright.sync_api import expect
    with browser(loyalty) as (page,errors,calls):
        open_order(page,loyalty)
        page.evaluate("""()=>{const original=fetch;window.fetch=async(...args)=>{const response=await original(...args);if(String(args[0]).includes('/api/customer-registration/search?')&&String(args[0]).includes('94771111111'))return new Promise(resolve=>{window.releaseOldLookup=()=>resolve(response);});return response;};}""")
        page.locator('#order-phone').fill('94771111111');page.wait_for_function('typeof window.releaseOldLookup==="function"')
        expect(page.locator('#order-lookup-status')).to_have_attribute('data-state','loading')
        page.locator('#order-phone').fill('96550012345')
        expect(page.locator('#order-customer-country')).to_contain_text('Kuwait')
        page.evaluate('window.releaseOldLookup()')
        expect(page.locator('#order-phone')).to_have_value('+96550012345')
        expect(page.locator('#order-customer-country')).to_contain_text('Kuwait')
        assert not errors


def test_lost_registration_response_reuses_key_without_duplicate_customer(loyalty):
    from playwright.sync_api import expect
    with browser(loyalty) as (page,errors,calls):
        open_order(page,loyalty)
        expect(page.locator('#order-lookup-status')).to_have_attribute('data-state','empty')
        page.evaluate("""()=>{const original=fetch;let lost=false;window.fetch=async(...args)=>{const response=await original(...args);if(String(args[0]).endsWith('/api/customer-registration/register')&&!lost){lost=true;throw new TypeError('synthetic lost response');}return response;};}""")
        page.locator('#order-identity-confirm').check();page.locator('#order-register-customer').click()
        expect(page.locator('#order-registration-feedback')).to_contain_text('network failure')
        expect(page.locator('#order-loyalty-customer')).to_have_value('')
        page.locator('#order-register-customer').click()
        expect(page.locator('#order-registration-feedback')).to_contain_text('Customer registered and selected')
        with loyalty.sessions() as db:
            assert db.query(Customer).filter_by(name='Synthetic purchaser').count()==1
            assert db.query(LoyaltyMember).count()==0 and db.query(JournalEntry).count()==0
        assert not errors
