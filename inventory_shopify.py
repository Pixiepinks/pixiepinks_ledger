"""Phase 3 inventory transport. Live writes remain source-gated pending contract evidence.

Only an approved durable document can call set_quantities. The proposed CAS/idempotent
contract is mocked, not certified by introspection alone. No retries are implemented.
"""
import re
import time
import httpx
from shopify_catalog_service import ShopifyCatalogClient
from retail_inventory import InventoryError, API_VERSION, TYPE, signature, fields
from settings import settings

# Do not flip using an environment variable. Requires authoritative reviewed evidence.
MUTATION_CONTRACT_VERIFIED = False
LOCATIONS = '''query InventoryLocations($after:String) {
 shop { myshopifyDomain } locations(first:50,after:$after) {
 nodes { id name isActive } pageInfo { hasNextPage endCursor }
 }}'''
READ = '''query InventoryObservation($id:ID!,$location:ID!) {
 shop { myshopifyDomain } inventoryItem(id:$id) { id tracked
 variant { id product { id status } }
 inventoryLevel(locationId:$location) { id location { id name isActive }
 quantities(names:["available","on_hand"]) { name quantity } }
 }}'''
META = 'inputFields { name type { '+TYPE+' } }'
CONTRACT = '''query InventoryContract {
 shop { myshopifyDomain } currentAppInstallation { accessScopes { handle } }
 mutation:__type(name:"Mutation") { fields { name args { name type { '''+TYPE+''' } } } }
 set:__type(name:"InventorySetQuantitiesInput") { '''+META+''' }
 quantity:__type(name:"InventoryQuantityInput") { '''+META+''' }
 __schema { directives { name args { name type { '''+TYPE+''' } } } }
}'''
SET = '''mutation InventorySet($input:InventorySetQuantitiesInput!,$key:String!) @idempotent(key:$key) {
 inventorySetQuantities(input:$input) {
 inventoryAdjustmentGroup { id }
 userErrors { field message code }
 }}'''


def gid(value,kind):
    return isinstance(value,str) and re.fullmatch(r'gid://shopify/'+kind+r'/[0-9]{1,30}',value) is not None


class InventoryShopify(ShopifyCatalogClient):
    def request(self,query,variables,mutation=False):
        if query not in {LOCATIONS,READ,CONTRACT,SET} or (query==SET)!=mutation:
            raise InventoryError('UNSUPPORTED_OPERATION')
        if mutation and (not MUTATION_CONTRACT_VERIFIED or not settings.SHOPIFY_INVENTORY_WRITE_ENABLED):
            raise InventoryError('INVENTORY_WRITES_BLOCKED')
        try:
            # Existing authentication/token management. No forced token refresh or retries.
            token=self.get_token()
            response=self.http.post(f'{self.base_url}/admin/api/{API_VERSION}/graphql.json',
                headers={'X-Shopify-Access-Token':token},json={'query':query,'variables':variables},timeout=httpx.Timeout(8,connect=3))
            if response.status_code!=200:raise InventoryError('INVENTORY_API_UNAVAILABLE')
            payload=response.json()
            if response.headers.get('X-Shopify-API-Version')!=API_VERSION:raise InventoryError('INVENTORY_API_VERSION_UNVERIFIED')
            if not isinstance(payload,dict) or payload.get('errors') or not isinstance(payload.get('data'),dict):raise InventoryError('INVENTORY_GRAPHQL_ERROR')
            data=payload['data']
            # Mutations don't select shop; target endpoint already bound to configured shop,
            # and the workflow's fresh preflight verifies it before committing dispatch intent.
            if not mutation and (data.get('shop') or {}).get('myshopifyDomain','').lower()!=self.base_url.removeprefix('https://').lower():
                raise InventoryError('INVENTORY_SHOP_MISMATCH')
            return data
        except InventoryError:raise
        except Exception:raise InventoryError('INVENTORY_API_UNCERTAIN' if mutation else 'INVENTORY_READ_UNAVAILABLE') from None

    def capabilities(self):
        data=self.request(CONTRACT,{})
        scopes={v.get('handle') for v in (data.get('currentAppInstallation') or {}).get('accessScopes',[]) if isinstance(v,dict)}
        result={'api_version':API_VERSION,'read_inventory':bool(scopes & {'read_inventory','write_inventory'}),
            'read_locations':bool(scopes & {'read_locations','write_locations'}),'write_inventory':'write_inventory' in scopes,
            'source_contract_verified':MUTATION_CONTRACT_VERIFIED,'mutation_shape_verified':False,
            'native_transfers_verified':False}
        try:
            inputs={f['name']:signature(f['type']) for f in data['set']['inputFields']}
            quantity={f['name']:signature(f['type']) for f in data['quantity']['inputFields']}
            mutation=next(f for f in data['mutation']['fields'] if f['name']=='inventorySetQuantities')
            args={a['name']:signature(a['type']) for a in mutation['args']}
            directive=next(d for d in data['__schema']['directives'] if d['name']=='idempotent')
            dargs={a['name']:signature(a['type']) for a in directive['args']}
            result['mutation_shape_verified']=(all(inputs.get(k)==v for k,v in {'name':'String','reason':'String','referenceDocumentUri':'String','quantities':'[InventoryQuantityInput]'}.items())
                and all(quantity.get(k)==v for k,v in {'inventoryItemId':'ID','locationId':'ID','quantity':'Int','compareQuantity':'Int'}.items())
                and args.get('input')=='InventorySetQuantitiesInput' and dargs.get('key')=='String')
        except (KeyError,TypeError,StopIteration):pass
        return result

    def locations(self):
        result=[];cursor=None;seen=set();deadline=time.monotonic()+20
        for _ in range(20):
            if time.monotonic()>deadline:raise InventoryError('LOCATION_BUDGET_EXCEEDED')
            data=self.request(LOCATIONS,{'after':cursor});conn=data.get('locations')
            if time.monotonic()>deadline:raise InventoryError('LOCATION_BUDGET_EXCEEDED')
            if not isinstance(conn,dict) or not isinstance(conn.get('nodes'),list) or len(conn['nodes'])>50:raise InventoryError('INCOMPLETE_LOCATIONS')
            for row in conn['nodes']:
                if not isinstance(row,dict) or not gid(row.get('id'),'Location') or any(r['id']==row['id'] for r in result) or not isinstance(row.get('name'),str) or type(row.get('isActive')) is not bool:raise InventoryError('INCOMPLETE_LOCATIONS')
                result.append(row)
            page=conn.get('pageInfo',{})
            if type(page.get('hasNextPage')) is not bool or 'endCursor' not in page:raise InventoryError('INCOMPLETE_LOCATIONS')
            if not page['hasNextPage']:return result
            cursor=page.get('endCursor')
            if not conn['nodes'] or not isinstance(cursor,str) or not cursor or cursor in seen:raise InventoryError('INCOMPLETE_LOCATIONS')
            seen.add(cursor)
        raise InventoryError('LOCATION_BUDGET_EXCEEDED')

    def observe(self,line):
        if not gid(line.get('inventory_item_id'),'InventoryItem') or not gid(line.get('location_id'),'Location'):raise InventoryError('INVENTORY_IDENTITY_INVALID')
        data=self.request(READ,{'id':line['inventory_item_id'],'location':line['location_id']})
        item=data.get('inventoryItem');variant=item.get('variant') if isinstance(item,dict) else None
        if not isinstance(variant,dict) or item.get('id')!=line['inventory_item_id'] or variant.get('id')!=line['variant_id'] or (variant.get('product') or {}).get('id')!=line['product_id']:raise InventoryError('INVENTORY_IDENTITY_CHANGED')
        if variant['product'].get('status')!='ACTIVE':raise InventoryError('INVENTORY_PRODUCT_INACTIVE')
        if item.get('tracked') is False:raise InventoryError('INVENTORY_UNTRACKED')
        if item.get('tracked') is not True:raise InventoryError('INVENTORY_TRACKING_UNVERIFIED')
        level=item.get('inventoryLevel')
        if not isinstance(level,dict):raise InventoryError('INVENTORY_NOT_STOCKED_AT_LOCATION')
        location=level.get('location') or {}
        if location.get('id')!=line['location_id'] or location.get('isActive') is not True or not isinstance(location.get('name'),str):raise InventoryError('INVENTORY_LOCATION_UNAVAILABLE')
        values=level.get('quantities')
        if not isinstance(values,list) or len(values)!=2 or any(not isinstance(v,dict) or type(v.get('quantity')) is not int for v in values):raise InventoryError('INVENTORY_QUANTITIES_UNVERIFIED')
        quantities={v.get('name'):v['quantity'] for v in values}
        if set(quantities)!={'available','on_hand'}:raise InventoryError('INVENTORY_QUANTITIES_UNVERIFIED')
        return {**quantities,'location_name':location['name']}

    def set_quantities(self,plan,reference,key):
        quantities=[{'inventoryItemId':p['inventory_item_id'],'locationId':p['location_id'],
            'quantity':p['after'],'compareQuantity':p['before']} for p in plan if p['after']!=p['before']]
        if not quantities:raise InventoryError('EMPTY_MUTATION')
        data=self.request(SET,{'input':{'name':'on_hand','reason':'correction','referenceDocumentUri':reference,
            'quantities':quantities},'key':key},mutation=True)
        result=data.get('inventorySetQuantities')
        if not isinstance(result,dict) or result.get('userErrors')!=[]:raise InventoryError('INVENTORY_MUTATION_REQUIRES_REVIEW')
        group=(result.get('inventoryAdjustmentGroup') or {}).get('id')
        if not gid(group,'InventoryAdjustmentGroup'):raise InventoryError('INVENTORY_MUTATION_UNCERTAIN')
        return group


client=InventoryShopify()
