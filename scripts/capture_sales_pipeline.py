import json
from PIL import Image
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path('docs/images/sales-pipeline');errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1648,'height':928});page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8866/login');page.locator('input[name=username]').fill('pipeline-visual-admin');page.locator('input[name=password]').fill('synthetic-visual-only');page.get_by_role('button',name='Sign in').click();page.goto('http://127.0.0.1:8866/crm/sales-pipeline');page.wait_for_selector('.pl-card');page.screenshot(animations='disabled',path=str(out/'desktop-1648-board.png'))
    for width,height in [(1920,1080),(1366,768)]:page.set_viewport_size({'width':width,'height':height});page.screenshot(animations='disabled',path=str(out/f'desktop-{width}-board.png'))
    page.set_viewport_size({'width':1648,'height':928});page.locator('[data-stage=PAYMENT] .pl-card').first.click();page.wait_for_selector('.pl-profile');page.screenshot(animations='disabled',path=str(out/'payment-selected.png'));page.get_by_role('button',name='Set Payment Date').click();page.screenshot(animations='disabled',path=str(out/'payment-form.png'));page.locator('#pl-edit-cancel').click()
    page.locator('[data-stage=FUTURE] .pl-card').first.click();page.wait_for_function("document.querySelector('#pl-detail-body h3')?.textContent.includes('Noah Test')");page.screenshot(animations='disabled',path=str(out/'future-selected.png'));page.get_by_role('button',name='Set Future Date').click();page.screenshot(animations='disabled',path=str(out/'future-form.png'));page.locator('#pl-edit-cancel').click();page.locator('#pl-filter').click();page.screenshot(animations='disabled',path=str(out/'filters.png'));page.locator('#pl-filter-cancel').click()
    page.set_viewport_size({'width':412,'height':915});page.locator('#pl-close').click();page.screenshot(animations='disabled',path=str(out/'android-board.png'));page.locator('[data-stage=NEW] .pl-card').first.click();page.wait_for_function("document.querySelector('#pl-detail-body h3')?.textContent.includes('Mira Test')");page.screenshot(animations='disabled',path=str(out/'android-details.png'))
    assert not errors, errors
    print(json.dumps({'screenshots':{v.name:list(Image.open(v).size) for v in out.glob('*.png')},'browser_errors':errors,'mobile_document_overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth')}))
    browser.close()
