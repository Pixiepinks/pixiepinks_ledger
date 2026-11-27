from types import SimpleNamespace
from pathlib import Path
import pytest
from test_sales_pipeline_reconciliation import candidate,fixture,inbox,add_candidate
from test_loyalty_browser import browser
from models import User,SalesOrder

@pytest.mark.parametrize('width,height',[(1920,1080),(1648,928),(1366,768),(412,915)])
def test_admin_review_bulk_investigation_and_history(candidate,width,height):
    from playwright.sync_api import expect
    client,sessions,ids,csrf,oid=candidate
    with sessions() as db:
        uid=db.query(User).filter_by(role='ADMIN').one().id
        second,orderid=add_candidate(db,uid,601)
        missing,_=add_candidate(db,uid,602,status='PAID')
        # Synthetic missing and ambiguous cases; preserve production data by fixture isolation.
        db.delete(db.query(SalesOrder).filter_by(crm_conversation_id=db.query(__import__('sales_pipeline_models').SalesOpportunity).get(missing).conversation_id).one())
        amb,amb_order=add_candidate(db,uid,603)
        existing=db.get(SalesOrder,amb_order)
        db.add(SalesOrder(internal_order_number='SYNTHETIC-AMBIGUOUS',idempotency_key='synthetic-ambiguous',crm_conversation_id=existing.crm_conversation_id,customer_name='Synthetic Ambiguous',whatsapp_phone=existing.whatsapp_phone,created_by='test',order_status='DELIVERED',grand_total=350))
        db.commit()
    folder=Path('docs/images/sales-pipeline-reconciliation');folder.mkdir(parents=True,exist_ok=True)
    def shot(page,name):page.screenshot(path=str(folder/f'{width}-{name}.png'),full_page=True)
    with browser(SimpleNamespace(client=client),width) as (page,errors,calls):
        page.set_viewport_size({'width':width,'height':height})
        page.goto('https://loyalty.test/crm/sales-pipeline')
        expect(page.locator('.pl-reconciliation')).to_contain_text('2 order associations require review')
        shot(page,'pipeline-entry')
        page.locator('.pl-reports').evaluate('(e)=>e.open=true')
        page.locator('#pl-reconcile-indicator').click()
        expect(page.locator('#rec-rows tr')).to_have_count(2)
        shot(page,'dashboard')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        for index in (0,1):
            page.locator('#rec-rows button').nth(index).click();expect(page.locator('#rec-detail')).to_be_visible()
            expect(page.locator('#rec-evidence')).to_contain_text('Evidence comparison')
            if index==0:
                shot(page,'individual-evidence');page.locator('#rec-single').click();expect(page.locator('#rec-confirm')).to_be_visible();shot(page,'single-confirmation');page.locator('#rec-cancel').click()
                # Reopen original after cancelling confirmation.
                page.locator('#rec-rows button').nth(index).click()
            page.locator('#rec-reviewed').click();expect(page.locator('#rec-detail')).not_to_be_visible()
        page.locator('#rec-select').click();expect(page.locator('#rec-bulk')).to_contain_text('(2)')
        page.locator('#rec-bulk').click();expect(page.locator('#rec-confirm')).to_be_visible();shot(page,'bulk-preview')
        page.locator('#rec-confirm [name=confirmed]').check();page.locator('#rec-confirm [name=note]').fill('Synthetic administrator review; no production activity.')
        page.locator('#rec-confirm .rec-primary').click();expect(page.locator('#rec-feedback')).to_contain_text('2 existing order association(s) saved')
        shot(page,'reconciled-history')
        for tab,name in [('missing','missing-investigation'),('payment','payment-warning'),('conflict','ambiguous')]:
            page.locator(f'[data-tab={tab}]').click();expect(page.locator('#rec-feedback')).to_contain_text('Local evidence loaded');shot(page,name)
        page.locator('[data-tab=missing]').click();page.locator('#rec-rows button').first.click();page.locator('#rec-note').fill('Supporting evidence required for this synthetic example.');shot(page,'investigation-panel')
        page.locator('#rec-investigate').click();expect(page.locator('#rec-detail')).not_to_be_visible()
        page.goto('https://loyalty.test/crm/sales-pipeline');expect(page.locator('[data-stage=CONFIRMED] .pl-count')).to_have_text('2');shot(page,'confirmed-after')
        if width>750:assert page.evaluate('document.documentElement.scrollHeight<=innerHeight+1')
        assert not errors,errors

def test_pipeline_return_retains_selection_and_scroll(candidate):
    from playwright.sync_api import expect
    from scripts.seed_sales_pipeline_scale import seed
    seed(candidate[1],1000)
    with browser(SimpleNamespace(client=candidate[0]),1648) as (page,errors,calls):
        page.goto('https://loyalty.test/crm/sales-pipeline')
        cards=page.locator('[data-stage=INTERESTED] .pl-cards');expect(cards.locator('.pl-card')).to_have_count(50)
        cards.locator('.pl-card').first.click();expect(page.locator('.pl-profile')).to_be_visible()
        selected=page.url
        cards.evaluate('e=>e.scrollTop=300')
        page.locator('.pl-reports').evaluate('e=>e.open=true');page.locator('#pl-reconcile-indicator').click();expect(page.locator('#rec-summary article')).to_have_count(5)
        page.get_by_role('link',name='Back to Sales Pipeline').click();expect(page.locator('.pl-profile')).to_be_visible()
        expect(page).to_have_url(selected)
        assert page.locator('[data-stage=INTERESTED] .pl-cards').evaluate('e=>e.scrollTop')==300
        assert not errors
