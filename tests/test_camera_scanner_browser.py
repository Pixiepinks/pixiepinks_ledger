"""Mobile Chromium with synthetic MediaStreams; no physical Android claim."""
import io,base64,json
from pathlib import Path
from contextlib import contextmanager
import pytest
from test_camera_scanner import retail,inbox,node,import_nodes
import retail_inventory

CAMERA_MOCK=r'''
window.cameraRequests=[];window.cameraStreams=[];window.cameraCode='000012';
Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async opts=>{window.cameraRequests.push(opts);if(window.denyCamera)throw new DOMException('test denied','NotAllowedError');const c=document.createElement('canvas');c.width=640;c.height=480;const ctx=c.getContext('2d');ctx.fillStyle='white';ctx.fillRect(0,0,640,480);const s=c.captureStream(10);window.cameraStreams.push(s);return s;}}});
window.BarcodeDetector=class{static async getSupportedFormats(){return ['code_128','ean_13','ean_8','upc_a','upc_e'];}async detect(){return [{rawValue:window.cameraCode,format:'code_128'},{rawValue:window.cameraCode,format:'code_128'}];}};
'''

@contextmanager
def browser(r,monkeypatch,mock=True):
    from playwright.sync_api import sync_playwright
    root=Path(__file__).parents[1];calls=[]
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);page=b.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        if mock:page.add_init_script(CAMERA_MOCK)
        def route(h):
            req=h.request;url=req.url;path=url.removeprefix('https://camera.test')
            if url.startswith('https://cdn.shopify.com/'):
                if 'broken' in url:h.fulfill(status=404,body='');return
                h.fulfill(content_type='image/svg+xml',body='<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240"><rect width="240" height="240" fill="#fce7f3"/><circle cx="120" cy="95" r="60" fill="#f9a8d4"/><text x="58" y="195" font-size="22">Product image</text></svg>');return
            if path.startswith('/static/'):
                f=root/path.split('?',1)[0].lstrip('/')
                if f.is_file():h.fulfill(path=str(f));return
            if path.startswith('/crm/retail/'):
                if '/api/' in path:calls.append((req.method,path));assert req.method=='GET'
                response=r.client.get(path);h.fulfill(status=response.status_code,body=response.content,content_type=response.headers.get('content-type','text/plain'));return
            h.fulfill(status=404,body='')
        page.route('**/*',route);page.goto('https://camera.test/crm/retail/scanner')
        try:yield page,errors,calls
        finally:b.close()


def test_camera_decode_product_image_stock_and_cleanup(retail,monkeypatch):
    r=retail;import_nodes(r,[node(1,barcode='000012',sku='SKU-1',price='140.00')])
    def stock(s,**kw):return {'status':'VERIFIED','available':9,'active_available':9,'locations':[{'location':{'id':'shop','name':'Shop','isActive':True},'available':9,'on_hand':12,'reserved':2,'committed':1}],'read_at':'2026-11-23T05:00:00Z','cached':True,'age_seconds':1,'valid_for_seconds':20}
    monkeypatch.setattr(retail_inventory.client,'lookup',stock)
    with browser(r,monkeypatch) as (page,errors,calls):
        page.get_by_role('button',name='Scan with Camera',exact=True).click();page.get_by_role('heading',name='Stand Fan 1',exact=True).wait_for();page.locator('.scanner-stock').get_by_text('9 available',exact=True).wait_for()
        assert page.evaluate("cameraRequests[0].video.facingMode.ideal")== 'environment'
        assert page.evaluate("cameraStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))")
        assert len([v for v in calls if '/api/scan?' in v[1]])==1
        assert page.locator('.scanner-price').inner_text()=='Rs. 140.00'
        assert 'Reserved: 2' in page.locator('.scanner-stock').inner_text()
        page.get_by_role('button',name='Enlarge product image').click();page.locator('#scanner-image-dialog img').wait_for();page.locator('#scanner-image-close').click()
        assert page.get_by_role('button',name='Print label',exact=True).count()==0
        out=Path('/tmp/camera-scanner-ui');out.mkdir(exist_ok=True);page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(out/'product-mobile.png'),full_page=True)
        page.get_by_role('button',name='Scan next',exact=True).click();page.wait_for_function('cameraRequests.length===2');page.get_by_role('heading',name='Stand Fan 1',exact=True).wait_for()
        assert not errors


def test_permission_denied_manual_and_hidden_cleanup(retail,monkeypatch):
    r=retail
    with browser(r,monkeypatch) as (page,errors,calls):
        page.evaluate('window.denyCamera=true');page.locator('#scanner-camera-open').click();page.locator('#scanner-camera-status').get_by_text('permission denied',exact=False).wait_for();page.locator('#scanner-camera-manual').click();assert not page.locator('#scanner-camera-dialog').is_visible()
        page.evaluate('window.denyCamera=false;window.cameraCode=""');page.locator('#scanner-camera-open').click();page.wait_for_function('cameraStreams.length===1');page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'))")
        assert page.evaluate("cameraStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))")
        assert not page.locator('#scanner-camera-dialog').is_visible() and not errors


def test_duplicate_match_and_broken_image_placeholder(retail,monkeypatch):
    r=retail;a=node(1,barcode='SHARED');a['image']={'url':'https://cdn.shopify.com/broken.png'};import_nodes(r,[a,node(2,barcode='SHARED')]);monkeypatch.setattr(retail_inventory.client,'lookup',lambda s,**kw:retail_inventory.unavailable('OFFLINE'))
    with browser(r,monkeypatch) as (page,errors,calls):
        page.evaluate('window.cameraCode="SHARED"');page.locator('#scanner-camera-open').click();page.locator('.scanner-choice').first.wait_for();assert page.locator('.scanner-choice').count()==2 and page.locator('#scanner-result').is_hidden()
        page.locator('.scanner-choice').first.click();page.get_by_text('Image unavailable',exact=True).wait_for();assert page.get_by_role('button',name='Enlarge product image').is_disabled();assert not errors


@pytest.mark.parametrize('fmt,value',[('Code128','00Ab0012'),('EAN13','4792210130832'),('EAN8','96385074'),('UPCA','123456789012')])
def test_actual_vendored_decoder_formats(retail,monkeypatch,fmt,value):
    import zxingcpp
    from PIL import Image
    buffer=io.BytesIO()
    if fmt=='EAN8':
        from barcode import EAN8
        from barcode.writer import ImageWriter
        EAN8(value,writer=ImageWriter()).write(buffer,options={'write_text':False,'module_width':.5,'module_height':20,'quiet_zone':8})
    else:
        im=zxingcpp.write_barcode(getattr(zxingcpp.BarcodeFormat,fmt),value,640,180,20);view=memoryview(im);original=Image.frombytes('L',(view.shape[1],view.shape[0]),view.tobytes());padded=Image.new('L',(original.width+200,original.height),255);padded.paste(original,(100,0));padded.save(buffer,format='PNG')
    url='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()
    with browser(retail,monkeypatch,mock=False) as (page,errors,calls):
        result=page.evaluate('''async url=>{const image=new Image();image.src=url;await image.decode();const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;canvas.getContext('2d').drawImage(image,0,0);const decoder=await PixieCamera.decoder(true);return (await decoder.decode(canvas)).map(v=>v.value);}''',url)
        assert result==[value] and not errors
        if fmt=='Code128':
            recovered=page.evaluate('''async url=>{window.BarcodeDetector=class {static async getSupportedFormats(){return PixieCamera.FORMATS}async detect(){throw new Error('synthetic native failure')}};const image=new Image();image.src=url;await image.decode();const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;canvas.getContext('2d').drawImage(image,0,0);return (await (await PixieCamera.decoder()).decode(canvas)).map(v=>v.value);}''',url)
            assert recovered==[value] and not errors


def test_native_upce_exact_and_blank_fallback(retail,monkeypatch):
    with browser(retail,monkeypatch) as (page,errors,calls):
        value=page.evaluate('''async()=>{window.BarcodeDetector=class {static async getSupportedFormats(){return PixieCamera.FORMATS}async detect(){return [{rawValue:'01234565',format:'upc_e'}]}};const decoder=await PixieCamera.decoder();return await decoder.decode(document.createElement('canvas'));}''')
        assert value==[{'value':'01234565','format':'upc_e'}]
        blank=page.evaluate('''async()=>{const canvas=document.createElement('canvas');canvas.width=300;canvas.height=100;const c=canvas.getContext('2d');c.fillStyle='white';c.fillRect(0,0,300,100);return await (await PixieCamera.decoder(true)).decode(canvas);}''')
        assert blank==[] and not errors


def test_torch_and_navigation_cleanup(retail,monkeypatch):
    with browser(retail,monkeypatch) as (page,errors,calls):
        page.evaluate('''()=>{window.cameraCode='';const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);window.torchRequests=[];navigator.mediaDevices.getUserMedia=async options=>{const stream=await original(options);const track=stream.getVideoTracks()[0];track.getCapabilities=()=>({torch:true});track.applyConstraints=async v=>torchRequests.push(v);return stream;};}''')
        page.locator('#scanner-camera-open').click();page.locator('#scanner-camera-torch').wait_for();page.locator('#scanner-camera-torch').click();page.wait_for_function('torchRequests.length===1');page.wait_for_function("document.getElementById('scanner-camera-torch').getAttribute('aria-pressed')==='true'")
        assert page.evaluate('torchRequests[0].advanced[0].torch') is True
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide'))")
        assert page.evaluate("cameraStreams.length===1 && cameraStreams.every(s=>s.getTracks().every(t=>t.readyState==='ended'))")
        assert not errors


def test_decoder_download_failure_can_retry(retail,monkeypatch):
    with browser(retail,monkeypatch,mock=False) as (page,errors,calls):
        attempts=[]
        def script(route):
            attempts.append(1)
            if len(attempts)==1:route.fulfill(content_type='application/javascript',body='/* synthetic incomplete download */')
            else:route.fallback()
        page.route('**/static/vendor/zxing/**',script)
        assert page.evaluate('''async()=>{try{await PixieCamera.decoder(true);return false}catch(_){return true}}''') is True
        assert 'ZXing local fallback' in page.evaluate('''async()=>(await PixieCamera.decoder(true)).name''')
        assert len(attempts)==2 and not errors
