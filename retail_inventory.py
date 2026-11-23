"""Targeted read-only scanner inventory. No catalog scan, inventory write or DDL."""
import copy
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime,timezone
import httpx
from shopify_catalog_service import ShopifyCatalogClient
from settings import settings

API_VERSION='2026-10'  # Does not change global Shopify API/webhook settings.
TYPE='kind name ofType { kind name ofType { kind name ofType { kind name } } }'
FIELDS='fields { name type { '+TYPE+' } args { name type { '+TYPE+' } } }'
PROBE='''query RetailScannerInventoryContract {
 shop { myshopifyDomain } currentAppInstallation { accessScopes { handle } }
 item:__type(name:"InventoryItem") { '''+FIELDS+''' }
 variant:__type(name:"ProductVariant") { '''+FIELDS+''' }
 connection:__type(name:"InventoryLevelConnection") { '''+FIELDS+''' }
 level:__type(name:"InventoryLevel") { '''+FIELDS+''' }
 quantity:__type(name:"InventoryQuantity") { '''+FIELDS+''' }
 page:__type(name:"PageInfo") { '''+FIELDS+''' }
 location:__type(name:"Location") { '''+FIELDS+''' }
}'''
READ='''query RetailScannerAvailable($id:ID!,$after:String,$inventory:Boolean!,$locations:Boolean!) {
 shop { myshopifyDomain }
 productVariant(id:$id) { id product { id status }
  inventoryItem { id tracked
   inventoryLevels(first:50,after:$after) @include(if:$inventory) {
    nodes { id quantities(names:["available"]) { name quantity }
     location @include(if:$locations) { id name isActive }
    } pageInfo { hasNextPage endCursor }
   }
  }
 }
}'''
DETAIL_READ=READ.replace('names:["available"]','names:["available","on_hand","reserved","committed"]')
TTL=20
FAILURE_TTL=5
MAX_SECONDS=12
MAX_PAGES=20
MAX_LEVELS=1000


class InventoryError(Exception):
    def __init__(self,reason):self.reason=reason;super().__init__(reason)


def signature(value):
    if not isinstance(value,dict):return '?'
    if value.get('kind')=='NON_NULL':return signature(value.get('ofType'))
    if value.get('kind')=='LIST':return '['+signature(value.get('ofType'))+']'
    return value.get('name') or '?'


def fields(value):
    rows=value.get('fields') if isinstance(value,dict) else None
    if not isinstance(rows,list):return {}
    return {r['name']:r for r in rows if isinstance(r,dict) and isinstance(r.get('name'),str)}


def shape(data):
    required={'item':{'id':'ID','tracked':'Boolean','inventoryLevels':'InventoryLevelConnection'},
        'variant':{'id':'ID','product':'Product','inventoryItem':'InventoryItem'},
        'connection':{'nodes':'[InventoryLevel]','pageInfo':'PageInfo'},
        'level':{'id':'ID','quantities':'[InventoryQuantity]','location':'Location'},
        'quantity':{'name':'String','quantity':'Int'},'page':{'hasNextPage':'Boolean','endCursor':'String'},
        'location':{'id':'ID','name':'String','isActive':'Boolean'}}
    for group,expected in required.items():
        got=fields(data.get(group))
        if any(signature(got.get(name,{}).get('type'))!=kind for name,kind in expected.items()):return False
    args={a['name']:signature(a.get('type')) for a in fields(data['item'])['inventoryLevels'].get('args',[]) if isinstance(a,dict) and 'name' in a}
    qty={a['name']:signature(a.get('type')) for a in fields(data['level'])['quantities'].get('args',[]) if isinstance(a,dict) and 'name' in a}
    return args.get('first')=='Int' and args.get('after')=='String' and qty.get('names')=='[String]'


def unavailable(reason):
    return {'status':'UNAVAILABLE','message':'Stock quantity unavailable','reason':reason,'available':None,
        'tracked':None,'locations':[],'read_at':None,'cached':False,'age_seconds':None,'valid_for_seconds':FAILURE_TTL}


class InventoryClient(ShopifyCatalogClient):
    def __init__(self,http_client=httpx,clock=time.monotonic):
        super().__init__(http_client=http_client)
        self.elapsed=clock;self.lock=threading.Lock();self.cache=OrderedDict();self.probes={}

    def _read(self,query,variables):
        if query not in {PROBE,READ,DETAIL_READ}:raise InventoryError('UNSUPPORTED_READ')
        try:
            response=self.http.post(f'{self.base_url}/admin/api/{API_VERSION}/graphql.json',
                headers={'X-Shopify-Access-Token':self.get_token()},json={'query':query,'variables':variables},
                timeout=httpx.Timeout(8,connect=3))
            if response.status_code==429:raise InventoryError('RATE_LIMITED')
            if response.status_code in {401,403}:raise InventoryError('ACCESS_UNAVAILABLE')
            if response.status_code!=200:raise InventoryError('SERVICE_UNAVAILABLE')
            payload=response.json()
            if not isinstance(payload,dict) or payload.get('errors') or not isinstance(payload.get('data'),dict):raise InventoryError('ACCESS_OR_SCHEMA_UNAVAILABLE')
            if response.headers.get('X-Shopify-API-Version')!=API_VERSION:raise InventoryError('API_VERSION_UNVERIFIED')
            data=payload['data']
            if (data.get('shop') or {}).get('myshopifyDomain','').lower()!=self.base_url.removeprefix('https://').lower():raise InventoryError('SHOP_IDENTITY_UNVERIFIED')
            return data
        except InventoryError:raise
        except Exception:raise InventoryError('SERVICE_UNAVAILABLE') from None

    def capabilities(self):
        key=((settings.SHOPIFY_SHOP or '').lower(),settings.SHOPIFY_CLIENT_ID)
        with self.lock:
            cached=self.probes.get(key)
            if cached and self.elapsed()<cached[0]:return cached[1]
        data=self._read(PROBE,{})
        if not shape(data):raise InventoryError('INVENTORY_SCHEMA_UNVERIFIED')
        values=(data.get('currentAppInstallation') or {}).get('accessScopes')
        if not isinstance(values,list):raise InventoryError('ACCESS_UNAVAILABLE')
        scopes={v.get('handle') for v in values if isinstance(v,dict)}
        if not scopes.intersection({'read_products','write_products'}):raise InventoryError('PRODUCT_SCOPE_UNAVAILABLE')
        result={'inventory':bool(scopes.intersection({'read_inventory','write_inventory'})),
            'locations':bool(scopes.intersection({'read_locations','write_locations'}))}
        with self.lock:
            if len(self.probes)>16:self.probes.clear()
            self.probes[key]=(self.elapsed()+120,result)
        return result

    def lookup(self,snapshot,detailed=False):
        # Full identity + app + local version prevents cross-shop/reimport cache reuse.
        key=(settings.SHOPIFY_SHOP,settings.SHOPIFY_CLIENT_ID,detailed,*[snapshot.get(k) for k in ('shop','variant_id','product_id','inventory_item_id','version')])
        with self.lock:
            cached=self.cache.get(key)
            if cached and self.elapsed()<cached[0]:
                self.cache.move_to_end(key);value=copy.deepcopy(cached[1]);value['cached']=True
                if value['read_at'] is not None:value['age_seconds']=round(max(0,self.elapsed()-cached[2]),1)
                return value
        try:
            if not self.configured():raise InventoryError('CONNECTION_UNAVAILABLE')
            if snapshot.get('shop')!=(settings.SHOPIFY_SHOP or '').lower():raise InventoryError('SHOP_IDENTITY_UNVERIFIED')
            if not re.fullmatch(r'gid://shopify/ProductVariant/[0-9]+',snapshot.get('variant_id') or '') or not re.fullmatch(r'gid://shopify/Product/[0-9]+',snapshot.get('product_id') or ''):raise InventoryError('PRODUCT_IDENTITY_UNVERIFIED')
            cap=self.capabilities();deadline=self.elapsed()+MAX_SECONDS
            after=None;cursors=set();levels=[];seen=set();seen_locations=set();identity=None
            for _ in range(MAX_PAGES):
                if self.elapsed()>deadline:raise InventoryError('READ_BUDGET_EXCEEDED')
                data=self._read(DETAIL_READ if detailed else READ,{'id':snapshot['variant_id'],'after':after,'inventory':cap['inventory'],'locations':cap['locations']})
                if self.elapsed()>deadline:raise InventoryError('READ_BUDGET_EXCEEDED')
                row=data.get('productVariant');item=row.get('inventoryItem') if isinstance(row,dict) else None
                product=row.get('product') if isinstance(row,dict) else None
                if not isinstance(item,dict) or not isinstance(product,dict) or row.get('id')!=snapshot['variant_id'] or product.get('id')!=snapshot['product_id']:raise InventoryError('PRODUCT_IDENTITY_UNVERIFIED')
                if product.get('status')!='ACTIVE':raise InventoryError('PRODUCT_INACTIVE')
                if not re.fullmatch(r'gid://shopify/InventoryItem/[0-9]+',item.get('id') or '') or type(item.get('tracked')) is not bool:raise InventoryError('INVENTORY_IDENTITY_UNVERIFIED')
                if snapshot.get('inventory_item_id') and snapshot['inventory_item_id']!=item['id']:raise InventoryError('INVENTORY_IDENTITY_CHANGED')
                current=(row['id'],product['id'],product['status'],item['id'],item['tracked'])
                if identity is not None and current!=identity:raise InventoryError('INVENTORY_CHANGED_DURING_READ')
                identity=current
                stamp=datetime.now(timezone.utc).isoformat()
                if item['tracked'] is False:
                    value={'status':'UNTRACKED','message':'Inventory is not tracked','tracked':False,'available':None,'locations':[],
                        'read_at':stamp,'cached':False,'age_seconds':0,'valid_for_seconds':TTL}
                    if detailed:value['active_available']=None
                    break
                if not cap['inventory']:
                    value=unavailable('INVENTORY_SCOPE_UNAVAILABLE');value.update(tracked=True);break
                connection=item.get('inventoryLevels')
                if not isinstance(connection,dict) or not isinstance(connection.get('nodes'),list) or len(connection['nodes'])>50:raise InventoryError('INCOMPLETE_INVENTORY')
                page=connection.get('pageInfo');nodes=connection['nodes']
                if not isinstance(page,dict) or type(page.get('hasNextPage')) is not bool or 'endCursor' not in page:raise InventoryError('INCOMPLETE_INVENTORY')
                for node in nodes:
                    if not isinstance(node,dict) or not isinstance(node.get('id'),str) or not node['id'] or node['id'] in seen:raise InventoryError('INCOMPLETE_INVENTORY')
                    seen.add(node['id']);qty=node.get('quantities')
                    if not isinstance(qty,list) or not qty or (not detailed and len(qty)!=1):raise InventoryError('AVAILABLE_QUANTITY_UNVERIFIED')
                    quantities={}
                    for q in qty:
                        if not isinstance(q,dict) or q.get('name') not in {'available','on_hand','reserved','committed'} or q['name'] in quantities or type(q.get('quantity')) is not int:raise InventoryError('AVAILABLE_QUANTITY_UNVERIFIED')
                        quantities[q['name']]=q['quantity']
                    if 'available' not in quantities:raise InventoryError('AVAILABLE_QUANTITY_UNVERIFIED')
                    location=None
                    if cap['locations']:
                        location=node.get('location')
                        if not isinstance(location,dict) or not isinstance(location.get('id'),str) or not isinstance(location.get('name'),str) or type(location.get('isActive')) is not bool:raise InventoryError('LOCATION_UNVERIFIED')
                        location={k:location[k] for k in ('id','name','isActive')}
                        if detailed and location['id'] in seen_locations:raise InventoryError('DUPLICATE_LOCATION')
                        seen_locations.add(location['id'])
                    levels.append({'level_id':node['id'],'location':location,'available':quantities['available'],**({k:quantities.get(k) for k in ('on_hand','reserved','committed')} if detailed else {})})
                    if len(levels)>MAX_LEVELS:raise InventoryError('READ_BUDGET_EXCEEDED')
                if not page['hasNextPage']:
                    value={'status':'VERIFIED','message':'Available across returned Shopify inventory levels','tracked':True,
                        'available':sum(v['available'] for v in levels),'locations':levels,'locations_accessible':cap['locations'],
                        'read_at':stamp,'cached':False,'age_seconds':0,'valid_for_seconds':TTL}
                    if detailed:value['active_available']=sum(v['available'] for v in levels if v['location']['isActive']) if cap['locations'] else None
                    break
                cursor=page['endCursor']
                if not nodes or not isinstance(cursor,str) or not cursor or len(cursor)>2048 or cursor in cursors:raise InventoryError('INCOMPLETE_PAGINATION')
                cursors.add(cursor);after=cursor
            else:raise InventoryError('READ_BUDGET_EXCEEDED')
        except InventoryError as error:value=unavailable(error.reason)
        except Exception:value=unavailable('SERVICE_UNAVAILABLE')
        ttl=TTL if value['status'] in {'VERIFIED','UNTRACKED'} else FAILURE_TTL
        with self.lock:
            self.cache[key]=(self.elapsed()+ttl,copy.deepcopy(value),self.elapsed());self.cache.move_to_end(key)
            while len(self.cache)>256:self.cache.popitem(last=False)
        return value


client=InventoryClient()
