"""Camera/stock tests never access physical cameras or production services."""
import copy,json,subprocess,base64,io
from pathlib import Path
from datetime import datetime
import httpx,pytest
from models import User,JournalEntry,SalesOrder
from retail_models import RetailVariant,RetailPreview
from inventory_models import InventoryDocument
from purchasing_models import PurchaseRecord
from test_whatsapp_quick_messages import inbox
from test_retail_phase1 import retail,node,import_nodes,choose,allocate
from test_retail_scanner import contract,page,SNAPSHOT
import retail_inventory as inventory


def detailed_client(monkeypatch,pages):
    for k,v in [('SHOPIFY_SHOP','retail-fixture'),('SHOPIFY_CLIENT_ID','fixture'),('SHOPIFY_CLIENT_SECRET','fixture')]:monkeypatch.setattr(inventory.settings,k,v)
    calls=[];pending=iter(pages)
    def transport(r):
        data=json.loads(r.content);calls.append(data);assert r.method=='POST' and data['query'] in {inventory.PROBE,inventory.DETAIL_READ} and 'mutation' not in data['query']
        return httpx.Response(200,json={'data':contract() if data['query']==inventory.PROBE else next(pending)},headers={'X-Shopify-API-Version':'2026-10'})
    c=inventory.InventoryClient(httpx.Client(transport=httpx.MockTransport(transport)));c._token='fixture';c._expires_at=10**20
    return c,calls


def test_detailed_inventory_unique_active_locations_and_explicit_reserved(monkeypatch):
    p=page(5);levels=p['productVariant']['inventoryItem']['inventoryLevels']['nodes'];levels[0]['quantities'] += [{'name':'on_hand','quantity':12},{'name':'reserved','quantity':2},{'name':'committed','quantity':5}]
    levels.append({'id':'warehouse-level','location':{'id':'warehouse','name':'Warehouse','isActive':True},'quantities':[{'name':'available','quantity':3},{'name':'on_hand','quantity':9},{'name':'reserved','quantity':1}]})
    levels.append({'id':'inactive-level','location':{'id':'inactive','name':'Closed location','isActive':False},'quantities':[{'name':'available','quantity':100}]})
    c,calls=detailed_client(monkeypatch,[p]);v=c.lookup(SNAPSHOT,detailed=True)
    assert v['status']=='VERIFIED' and v['active_available']==8 and v['locations'][0]['reserved']==2
    assert v['locations'][0]['on_hand']-v['locations'][0]['available']!=v['locations'][0]['reserved']
    assert v['locations'][2]['reserved'] is None
    assert c.lookup(SNAPSHOT,detailed=True)['cached'] and len(calls)==2


def test_duplicate_location_cannot_inflate_total(monkeypatch):
    p=page();a=p['productVariant']['inventoryItem']['inventoryLevels']['nodes'];a.append({**copy.deepcopy(a[0]),'id':'different-level-same-location'})
    c,_=detailed_client(monkeypatch,[p]);v=c.lookup(SNAPSHOT,detailed=True);assert v['status']=='UNAVAILABLE' and v['available'] is None and v['reason']=='DUPLICATE_LOCATION'


def test_grouped_readonly_endpoint_and_variant_image_priority(retail,monkeypatch):
    r=retail;a=node(1,barcode='001234',sku='SKU-1');a['image']={'url':'https://cdn.shopify.com/variant.png'};import_nodes(r,[a]);row=r.client.get('/crm/retail/api/scan?code=001234').json()['rows'][0];assert row['image'].endswith('variant.png')
    a['image']={'url':None};import_nodes(r,[a]);row=r.client.get('/crm/retail/api/scan?code=001234').json()['rows'][0];assert row['image']=='https://cdn.shopify.com/test.jpg'
    with r.sessions() as db:
        for gid,kind in [('shop','SHOP'),('warehouse','WAREHOUSE')]:db.add(PurchaseRecord(id=gid,shop='retail-fixture',kind='LOCATION',client_key=gid*16,business_key='location:'+gid,request_hash='0'*64,payload={'location_id':gid,'type':kind,'enabled':True},status='ACTIVE',actor_id=r.uid))
        db.commit()
    v={'status':'VERIFIED','available':9,'active_available':9,'locations':[{'location':{'id':'shop','name':'Shop','isActive':True},'available':5},{'location':{'id':'warehouse','name':'Warehouse','isActive':True},'available':4}]}
    monkeypatch.setattr(inventory.client,'lookup',lambda snapshot,**kw:copy.deepcopy(v))
    response=r.client.get(f"/crm/retail/api/scanner/products/{row['id']}/inventory",params={'version':row['version'],'details':'true'});assert response.status_code==200
    assert response.json()['by_type']=={'SHOP':5,'WAREHOUSE':4,'OTHER':None}
    with r.sessions() as db:assert db.query(JournalEntry).count()==0 and db.query(SalesOrder).count()==0 and db.query(InventoryDocument).count()==0 and db.query(RetailPreview).count()==0
    with r.sessions() as db:u=db.get(User,r.uid);u.role='STAFF';u.section_access=[];db.commit()
    assert r.client.get(f"/crm/retail/api/scanner/products/{row['id']}/inventory",params={'version':row['version'],'details':'true'}).status_code==403


def test_camera_lifecycle_node():
    root=Path(__file__).parents[1]
    code=r'''
const assert=require('assert'),{CameraScanner,valid,FORMATS}=require('./static/retail_camera.js');
const statuses=[],results=[],requests=[];let stopped=0,track={stop(){stopped++},getCapabilities(){return {torch:true}},applyConstraints:async()=>{},addEventListener(){}};
const video={pause(){},play:async()=>{},readyState:2,videoWidth:300,videoHeight:100};
const stream={getTracks:()=>[track],getVideoTracks:()=>[track]};
const c=new CameraScanner(video,{secure:true,mediaDevices:{getUserMedia:async opts=>{requests.push(opts);return stream}},decoderFactory:async()=>({name:'mock',decode:async()=>[{value:'00Ab0012',format:'code_128'},{value:'00Ab0012',format:'code_128'}],reset(){}}),onScan:v=>results.push(v),onStatus:v=>statuses.push(v)});
c.canvas={width:0,height:0,getContext:()=>({drawImage(){}})};
(async()=>{await c.start();await new Promise(r=>setTimeout(r,20));assert.deepEqual(results,['00Ab0012']);assert(stopped===1);assert(requests[0].audio===false&&requests[0].video.facingMode.ideal==='environment');assert(c.canvas.width===0);assert(FORMATS.includes('upc_e'));assert(valid('000123')&&valid('AB12')&&!valid('\x00BAD'));
let grant;const pending=new CameraScanner(video,{secure:true,mediaDevices:{getUserMedia:()=>new Promise(r=>grant=r)}});const promise=pending.start();pending.stop();grant(stream);await promise;assert(stopped===2);
const denied=new CameraScanner(video,{secure:true,mediaDevices:{getUserMedia:async()=>{throw {name:'NotAllowedError'}}},onStatus:v=>statuses.push(v)});await denied.start();assert(statuses.some(s=>s.includes('permission denied')));const insecure=new CameraScanner(video,{secure:false,onStatus:v=>statuses.push(v)});await insecure.start();assert(statuses.some(s=>s.includes('HTTPS')));
console.log('camera lifecycle passed');})().catch(e=>{console.error(e);process.exit(1)});
'''
    result=subprocess.run(['node','-e',code],cwd=root,capture_output=True,text=True,timeout=15);assert result.returncode==0,result.stderr
