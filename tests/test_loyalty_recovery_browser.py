"""Synthetic desktop/Android-width historical recovery, no live writes."""
from datetime import datetime
from pathlib import Path
import pytest
from models import Customer,User,SalesOrder,SalesPayment,JournalEntry
from loyalty_models import *
from test_loyalty import loyalty,inbox,source
from test_loyalty_browser import browser

@pytest.mark.parametrize('width',[390,1280])
def test_recovery_review_create_approve_and_migration_dates(loyalty,width):
    from playwright.sync_api import expect
    v=loyalty;oid=source(v,customer=False)
    with v.sessions() as db:
        o=db.get(SalesOrder,oid);o.customer_name='Reviewed buyer';o.whatsapp_phone='0779876543';db.commit()
    with browser(v,width) as (page,errors,calls):
        page.goto('https://loyalty.test/crm/loyalty/migration')
        expect(page.get_by_role('heading',name='Historical customer recovery')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#recovery-load').click();page.locator('#recovery-orders').get_by_role('button',name='Review identity evidence').click()
        expect(page.locator('#recovery-source-detail')).to_contain_text('Reviewed buyer')
        form=page.locator('#recovery-propose');form.locator('[name=action]').select_option('CREATE');form.get_by_role('button').click()
        expect(page.locator('#recovery-plan')).to_contain_text('PREVIEW')
        expect(page.locator('#recovery-execute [name=id]')).to_have_value('')
        with v.sessions() as db:assert db.query(Customer).count()==1 and db.query(LoyaltyMember).count()==0
        page.locator('#recovery-approve [name=reason]').fill('Individually reviewed original source identity')
        page.locator('#recovery-approve button').click();expect(page.locator('#recovery-plan')).to_contain_text('APPROVED')
        page.locator('#recovery-execute button').click();expect(page.locator('#recovery-plan')).to_contain_text('COMPLETE')
        with v.sessions() as db:
            assert db.query(Customer).count()==2 and db.query(LoyaltyMember).count()==0 and db.get(SalesOrder,oid).customer_id is None
            assert db.query(JournalEntry).count()==0 and db.query(SalesPayment).count()==1
        preview=page.locator('form[data-endpoint="/crm/loyalty/api/migrations"]')
        today=datetime.utcnow().strftime('%Y-%m-%d');preview.locator('[name=start]').fill(today);preview.locator('[name=end]').fill(today)
        preview.get_by_role('button',name='Preview historical customers').click()
        expect(page.locator('#loyalty-job-detail')).to_contain_text('eligible customers')
        with v.sessions() as db:
            job=db.query(LoyaltyMigration).one();assert job.filters['start'].endswith('18:30:00.000Z') and job.filters['end'].endswith('18:30:00.000Z')
            jid=job.id;digest=job.digest;assert job.status=='PREVIEW'
        expect(page.locator('#loyalty-approve-form [name=id]')).to_have_value(jid)
        expect(page.locator('#loyalty-execute-form [name=id]')).to_have_value('')
        page.goto('https://loyalty.test/crm/loyalty/migration/runs/'+jid)
        expect(page.locator('main.loyalty h1')).to_be_visible()
        expect(page.get_by_role('button',name='Copy Migration ID')).to_be_visible()
        expect(page.locator('#loyalty-approve-form [name=digest]')).to_have_value(digest)
        page.locator('#loyalty-approve-form [name=reason]').fill('Reviewed historical membership preview')
        page.locator('#loyalty-approve-form button').click()
        expect(page.locator('#loyalty-execute-form [name=digest]')).to_have_value(digest)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.evaluate('window.scrollTo(0,0)');page.screenshot(path=f'/tmp/loyalty2a-results-{width}.png',full_page=True)
        page.goto('https://loyalty.test/crm/loyalty/migration')
        page.evaluate('window.scrollTo(0,0)');page.screenshot(path=f'/tmp/loyalty2a-recovery-{width}.png',full_page=True)
        assert not errors,errors
    assert not v.sends
