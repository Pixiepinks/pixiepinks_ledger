"""Capture only the known synthetic localhost fixture; never production."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
out=Path('docs/images/sales-pipeline-refinement/after');out.mkdir(parents=True,exist_ok=True)
errors=[];measurements={}
def choose(page,card):
 name=card.locator('h4 > span').nth(1).text_content()
 card.click()
 page.wait_for_function("name=>document.querySelector('.pl-identity h3')?.textContent===name",arg=name)

with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 page=b.new_page(viewport={'width':1648,'height':928});page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto('http://127.0.0.1:8866/login');page.locator('[name=username]').fill('pipeline-visual-admin');page.locator('[name=password]').fill('synthetic-visual-only');page.get_by_role('button',name='Sign in').click()
 for width,height in [(1648,928),(1920,1080),(1366,768)]:
  page.set_viewport_size({'width':width,'height':height});page.goto('http://127.0.0.1:8866/crm/sales-pipeline');page.wait_for_selector('.pl-card')
  measurements[str(width)]=page.evaluate('({viewport:innerHeight,pageHeight:document.documentElement.scrollHeight,overflow:document.documentElement.scrollWidth>innerWidth})')
  page.screenshot(path=str(out/f'desktop-{width}-default.png'),animations='disabled')
  choose(page,page.locator('[data-stage=INTERESTED] .pl-card').first);page.screenshot(path=str(out/f'desktop-{width}-selected.png'),animations='disabled')
 page.set_viewport_size({'width':1648,'height':928});page.goto('http://127.0.0.1:8866/crm/sales-pipeline');page.wait_for_selector('.pl-card')
 page.locator('[data-stage=INTERESTED] .pl-cards').evaluate('e=>e.scrollTop=420');page.screenshot(path=str(out/'independent-scroll.png'),animations='disabled')
 measurements['independent_scroll']=page.evaluate("Object.fromEntries([...document.querySelectorAll('.pl-column')].map(c=>[c.dataset.stage,c.querySelector('.pl-cards').scrollTop]))")
 choose(page,page.locator('[data-stage=INTERESTED] .pl-card h4').filter(has_text='සිංහල').first.locator('..'));page.locator('.pl-profile').evaluate('e=>e.scrollTop=e.scrollHeight');page.screenshot(path=str(out/'multilingual-overdue.png'),animations='disabled')
 choose(page,page.locator('[data-stage=PAYMENT] .pl-card').first);page.get_by_role('button',name='Set Payment Date').click();page.screenshot(path=str(out/'payment-edit.png'),animations='disabled');page.locator('#pl-edit-cancel').click()
 choose(page,page.locator('[data-stage=FUTURE] .pl-card').first);page.get_by_role('button',name='Set Future Date').click();page.screenshot(path=str(out/'future-edit.png'),animations='disabled');page.locator('#pl-edit-cancel').click()
 page.locator('#pl-filter').click();f=page.locator('#pl-filters form');f.locator('[name=stage]').select_option('PAYMENT');f.locator('[name=due]').select_option('unknown');f.get_by_role('button',name='Apply filters').click();page.wait_for_function("document.querySelector('.pl-kpis').textContent.includes('Amounts Not Recorded')");page.screenshot(path=str(out/'unknown-amounts.png'),animations='disabled')
 page.set_viewport_size({'width':412,'height':915});page.goto('http://127.0.0.1:8866/crm/sales-pipeline');page.wait_for_selector('.pl-card');page.screenshot(path=str(out/'android-board.png'),animations='disabled');choose(page,page.locator('[data-stage=NEW] .pl-card').first);page.screenshot(path=str(out/'android-details.png'),animations='disabled')
 measurements['mobile_overflow']=page.evaluate('document.documentElement.scrollWidth>innerWidth');measurements['browser_errors']=errors
 print(json.dumps(measurements,indent=2));b.close()
