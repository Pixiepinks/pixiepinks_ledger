"""Phase 3 inventory transport, reviewed against official Shopify 2026-10 documentation.

Only an approved durable document can call apply. The reviewed delta/CAS/idempotent
contract is additionally checked against the live schema before dispatch. No retries are implemented.
"""
import re
import time
import hashlib
import json
from datetime import datetime
import httpx
from shopify_catalog_service import ShopifyCatalogClient
from retail_inventory import InventoryError, API_VERSION, TYPE, signature, fields
from settings import settings

# Official evidence: docs/inventory-2026-10-evidence.md. This is NOT write authorization.
MUTATION_CONTRACT_VERIFIED = True
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
META = 'inputFields { name defaultValue type { '+TYPE+' } }'
OBJECT = 'fields { name type { '+TYPE+' } args { name defaultValue type { '+TYPE+' } } }'
CONTRACT_VERSION = 'inventory-2026-10-delta-cas-v1'
# Runtime introspection verifies both operations and output types, not only input names.
CONTRACT = '''query InventoryContract {
 shop { myshopifyDomain } currentAppInstallation { accessScopes { handle } }
 mutation:__type(name:"Mutation") { '''+OBJECT+''' }
 set:__type(name:"InventorySetQuantitiesInput") { '''+META+''' }
 quantity:__type(name:"InventoryQuantityInput") { '''+META+''' }
 adjust:__type(name:"InventoryAdjustQuantitiesInput") { '''+META+''' }
 change:__type(name:"InventoryChangeInput") { '''+META+''' }
 setPayload:__type(name:"InventorySetQuantitiesPayload") { '''+OBJECT+''' }
 adjustPayload:__type(name:"InventoryAdjustQuantitiesPayload") { '''+OBJECT+''' }
 group:__type(name:"InventoryAdjustmentGroup") { '''+OBJECT+''' }
 setError:__type(name:"InventorySetQuantitiesUserError") { '''+OBJECT+''' }
 adjustError:__type(name:"InventoryAdjustQuantitiesUserError") { '''+OBJECT+''' }
 outputChange:__type(name:"InventoryChange") { '''+OBJECT+''' }
 __schema { directives { name locations args { name defaultValue type { '''+TYPE+''' } } } }
}'''
GROUP = '''inventoryAdjustmentGroup { id createdAt reason referenceDocumentUri
 changes(quantityNames:["available","on_hand"]) { name delta quantityAfterChange ledgerDocumentUri item { id } location { id } } } userErrors { field message code }'''
SET = '''mutation InventorySet($input:InventorySetQuantitiesInput!,$key:String!) {
 inventorySetQuantities(input:$input) @idempotent(key:$key) { '''+GROUP+''' }
}'''
ADJUST = '''mutation InventoryAdjust($input:InventoryAdjustQuantitiesInput!,$key:String!) {
 inventoryAdjustQuantities(input:$input) @idempotent(key:$key) { '''+GROUP+''' }
}'''
# Reasons remain a closed reviewed mapping; schema String alone is not proof of validity.
REASONS = {'RECEIPT':'received','DAMAGED':'damaged','LOST':'shrinkage',
           'CORRECTION':'correction','RETURN':'restock','MANUAL':'correction',
           'COUNT':'correction','OPENING':'correction'}


def exact_type(value):
    if not isinstance(value,dict):return '?'
    if value.get('kind')=='NON_NULL':return exact_type(value.get('ofType'))+'!'
    if value.get('kind')=='LIST':return '['+exact_type(value.get('ofType'))+']'
    return value.get('name') or '?'


def contract_checks(data):
    """Fail closed on required unsupported inputs, directive placement or output shape."""
    checks={}
    def inputs(group,expected):
        rows=(data.get(group) or {}).get('inputFields')
        if not isinstance(rows,list):return False
        values={r.get('name'):r for r in rows if isinstance(r,dict)}
        return (all(exact_type(values.get(k,{}).get('type'))==v for k,v in expected.items())
            and not any(k not in expected and exact_type(v.get('type')).endswith('!') and v.get('defaultValue') is None for k,v in values.items()))
    checks['set_input']=inputs('set',{'name':'String!','reason':'String!','referenceDocumentUri':'String','quantities':'[InventoryQuantityInput!]!'})
    checks['set_quantity']=inputs('quantity',{'inventoryItemId':'ID!','locationId':'ID!','quantity':'Int!','changeFromQuantity':'Int'})
    checks['adjust_input']=inputs('adjust',{'name':'String!','reason':'String!','referenceDocumentUri':'String','changes':'[InventoryChangeInput!]!'})
    checks['adjust_change']=inputs('change',{'inventoryItemId':'ID!','locationId':'ID!','delta':'Int!','changeFromQuantity':'Int','ledgerDocumentUri':'String'})
    mutations=fields(data.get('mutation'))
    for name,result in [('inventorySetQuantities','InventorySetQuantitiesPayload'),('inventoryAdjustQuantities','InventoryAdjustQuantitiesPayload')]:
        field=mutations.get(name,{})
        args={a.get('name'):a for a in field.get('args',[]) if isinstance(a,dict)}
        expected='InventorySetQuantitiesInput!' if name=='inventorySetQuantities' else 'InventoryAdjustQuantitiesInput!'
        checks[name]=(signature(field.get('type'))==result and exact_type(args.get('input',{}).get('type'))==expected
            and not any(k!='input' and exact_type(v.get('type')).endswith('!') and v.get('defaultValue') is None for k,v in args.items()))
    directive=next((d for d in (data.get('__schema') or {}).get('directives',[]) if d.get('name')=='idempotent'),{})
    dargs={a.get('name'):a for a in directive.get('args',[]) if isinstance(a,dict)}
    checks['idempotent_field']=(isinstance(directive.get('locations'),list) and 'FIELD' in directive['locations'] and exact_type(dargs.get('key',{}).get('type'))=='String!'
        and not any(k!='key' and exact_type(v.get('type')).endswith('!') and v.get('defaultValue') is None for k,v in dargs.items()))
    for group in ['setPayload','adjustPayload']:
        shape=fields(data.get(group));checks[group]=signature(shape.get('inventoryAdjustmentGroup',{}).get('type'))=='InventoryAdjustmentGroup' and signature(shape.get('userErrors',{}).get('type')) in {'[InventorySetQuantitiesUserError]','[InventoryAdjustQuantitiesUserError]'}
    shape=fields(data.get('group'))
    checks['group_output']=all(signature(shape.get(k,{}).get('type'))==v for k,v in {'id':'ID','createdAt':'DateTime','reason':'String','referenceDocumentUri':'String','changes':'[InventoryChange]'}.items())
    shape=fields(data.get('outputChange'))
    checks['change_output']=all(signature(shape.get(k,{}).get('type'))==v for k,v in {'name':'String','delta':'Int','quantityAfterChange':'Int','ledgerDocumentUri':'String','item':'InventoryItem','location':'Location'}.items())
    for key,name in [('setError','InventorySetQuantitiesUserErrorCode'),('adjustError','InventoryAdjustQuantitiesUserErrorCode')]:
        shape=fields(data.get(key));checks[key]=all(signature(shape.get(k,{}).get('type'))==v for k,v in {'field':'[String]','message':'String','code':name}.items())
    changes=fields(data.get('group')).get('changes',{})
    checks['change_filter']=any(a.get('name')=='quantityNames' and exact_type(a.get('type'))=='[String!]' for a in changes.get('args',[]))
    checks['output_arguments']=not any(exact_type(arg.get('type')).endswith('!') and arg.get('defaultValue') is None for key,names in {'group':['id','createdAt','reason','referenceDocumentUri','changes'],'outputChange':['name','delta','quantityAfterChange','ledgerDocumentUri','item','location'],'setPayload':['inventoryAdjustmentGroup','userErrors'],'adjustPayload':['inventoryAdjustmentGroup','userErrors'],'setError':['field','message','code'],'adjustError':['field','message','code']}.items() for name in names for arg in fields(data.get(key)).get(name,{}).get('args',[]))
    return checks


def gid(value,kind):
    return isinstance(value,str) and re.fullmatch(r'gid://shopify/'+kind+r'/[0-9]{1,30}',value) is not None


class InventoryShopify(ShopifyCatalogClient):
    def request(self,query,variables,mutation=False):
        if query not in {LOCATIONS,READ,CONTRACT,SET,ADJUST} or (query in {SET,ADJUST})!=mutation:
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

    def capabilities(self,include_contract=False):
        data=self.request(CONTRACT,{})
        scopes={v.get('handle') for v in (data.get('currentAppInstallation') or {}).get('accessScopes',[]) if isinstance(v,dict)}
        result={'api_version':API_VERSION,'read_inventory':bool(scopes & {'read_inventory','write_inventory'}),
            'read_locations':bool(scopes & {'read_locations','write_locations'}),'write_inventory':'write_inventory' in scopes,
            'source_contract_verified':MUTATION_CONTRACT_VERIFIED,'mutation_shape_verified':False,
            'native_transfers_verified':False}
        checks=contract_checks(data)
        result.update(contract_version=CONTRACT_VERSION,installed_scope_handles=sorted(scopes),
            contract_checks=checks,mutation_shape_verified=all(checks.values()),
            supported_reasons=sorted(set(REASONS.values())) if MUTATION_CONTRACT_VERIFIED else [],
            reason_semantics_verified=MUTATION_CONTRACT_VERIFIED)
        result['production_write_ready']=bool(MUTATION_CONTRACT_VERIFIED and result['write_inventory'] and result['read_inventory'] and result['read_locations'] and result['mutation_shape_verified'])
        result['release_blockers']=([k for k in ['read_inventory','read_locations','write_inventory','source_contract_verified','mutation_shape_verified'] if not result[k]])
        if include_contract:result['schema_evidence']=data
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

    @staticmethod
    def prepare(plan,reference,key,kind,reason=None):
        if kind not in {'RECEIPT','ADJUSTMENT','COUNT','OPENING'}:raise InventoryError('UNSUPPORTED_INVENTORY_KIND')
        if not isinstance(key,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,64}',key):raise InventoryError('INVALID_DURABLE_KEY')
        if not isinstance(reference,str) or not reference.startswith('urn:pixiepinks:inventory:'):raise InventoryError('INVALID_DOCUMENT_REFERENCE')
        if not isinstance(plan,list) or not 1<=len(plan)<=10:raise InventoryError('INVALID_MUTATION_PLAN')
        operation=SET if kind in {'COUNT','OPENING'} else ADJUST
        reason_code=REASONS.get(kind if kind!='ADJUSTMENT' else reason)
        if reason_code is None:raise InventoryError('UNSUPPORTED_INVENTORY_REASON')
        rows=[];seen=set()
        for index,p in enumerate(plan):
            pair=(p.get('inventory_item_id'),p.get('location_id'))
            if not gid(pair[0],'InventoryItem') or not gid(pair[1],'Location') or pair in seen:raise InventoryError('INVALID_MUTATION_IDENTITY')
            seen.add(pair)
            if any(type(p.get(k)) is not int for k in ('before','after')) or not 0<=p['after']<=2147483647:raise InventoryError('INVALID_MUTATION_QUANTITY')
            delta=p['after']-p['before']
            if not -1000000<=delta<=1000000:raise InventoryError('INVALID_MUTATION_QUANTITY')
            if kind=='RECEIPT' and delta<=0:raise InventoryError('INVALID_RECEIPT_DELTA')
            if delta==0:continue
            row={'inventoryItemId':pair[0],'locationId':pair[1]}
            if operation==SET:row.update(quantity=p['after'],changeFromQuantity=p['before'])
            else:
                if type(p.get('available_before')) is not int:raise InventoryError('DELTA_CAS_BASELINE_REQUIRED')
                if delta<0 and p['available_before']+delta<0:raise InventoryError('INSUFFICIENT_AVAILABLE_STOCK')
                row.update(delta=delta,changeFromQuantity=p['available_before'],ledgerDocumentUri=f'gid://pixiepinks/InventoryMovement/{key}-{index}')
            rows.append(row)
        if not rows:raise InventoryError('EMPTY_MUTATION')
        variables={'input':{'name':'on_hand' if operation==SET else 'available','reason':reason_code,'referenceDocumentUri':reference,
            'quantities' if operation==SET else 'changes':rows},'key':key}
        return {'query':operation,'variables':variables,'operation':'SET' if operation==SET else 'ADJUST','reason':reason_code}

    def apply(self,plan,reference,key,kind,reason=None,prepared=None):
        wire=self.prepare(plan,reference,key,kind,reason)
        if prepared is not None and prepared!=wire:raise InventoryError('DISPATCH_ENVELOPE_CHANGED')
        operation=wire['query'];reason_code=wire['reason'];rows=wire['variables']['input'].get('changes',wire['variables']['input'].get('quantities',[]))
        data=self.request(operation,wire['variables'],mutation=True)
        result=data.get('inventorySetQuantities' if operation==SET else 'inventoryAdjustQuantities')
        if not isinstance(result,dict) or result.get('userErrors')!=[]:raise InventoryError('INVENTORY_MUTATION_REQUIRES_REVIEW')
        group=result.get('inventoryAdjustmentGroup')
        if not isinstance(group,dict) or group.get('referenceDocumentUri')!=reference or group.get('reason')!=reason_code:raise InventoryError('INVENTORY_GROUP_CORRELATION_UNVERIFIED')
        try:
            stamp=datetime.fromisoformat(group['createdAt'].replace('Z','+00:00'))
            if stamp.utcoffset() is None:raise ValueError()
        except (KeyError,TypeError,ValueError):raise InventoryError('INVENTORY_GROUP_TIME_UNVERIFIED') from None
        group_id=group.get('id')
        if not isinstance(group_id,str) or len(group_id)>150 or not re.fullmatch(r'gid://shopify/InventoryAdjustmentGroup/[A-Za-z0-9_-]+',group_id):raise InventoryError('INVENTORY_GROUP_ID_UNVERIFIED')
        changes=group.get('changes')
        if not isinstance(changes,list) or not changes or any(not isinstance(c,dict) or not {'name','delta','quantityAfterChange','ledgerDocumentUri','item','location'}<=set(c) or not isinstance(c.get('name'),str) or type(c.get('delta')) is not int or (c.get('quantityAfterChange') is not None and type(c.get('quantityAfterChange')) is not int) for c in changes):raise InventoryError('INVENTORY_GROUP_CHANGES_UNVERIFIED')
        requested={(p['inventory_item_id'],p['location_id']):p for p in plan if p['after']!=p['before']}
        primary='on_hand' if operation==SET else 'available'
        found={};seen=set()
        for c in changes:
            item=c.get('item');location=c.get('location')
            if not isinstance(item,dict) or not isinstance(location,dict):raise InventoryError('INVENTORY_GROUP_ITEM_UNVERIFIED')
            pair=(item.get('id'),location.get('id'));change_key=(*pair,c['name'])
            if pair not in requested or c['name'] not in {'available','on_hand'} or change_key in seen:raise InventoryError('INVENTORY_GROUP_ITEM_UNVERIFIED')
            seen.add(change_key);expected=requested[pair]['after']-requested[pair]['before']
            if c['delta']!=expected:raise InventoryError('INVENTORY_GROUP_DELTA_UNVERIFIED')
            ledger=c.get('ledgerDocumentUri')
            if ledger is not None and not isinstance(ledger,str):raise InventoryError('INVENTORY_LEDGER_UNVERIFIED')
            if c['name']==primary:
                if operation==ADJUST and ledger is not None:
                    expected_ledger=next(r['ledgerDocumentUri'] for r in rows if (r['inventoryItemId'],r['locationId'])==pair)
                    if ledger!=expected_ledger:raise InventoryError('INVENTORY_LEDGER_UNVERIFIED')
                found[pair]=c
        if set(found)!=set(requested):raise InventoryError('INVENTORY_GROUP_DELTA_UNVERIFIED')
        # Both the actual Shopify ID and the complete returned evidence are retained.
        canonical={k:group[k] for k in ('id','createdAt','reason','referenceDocumentUri','changes')}
        fingerprint=hashlib.sha256(json.dumps(canonical,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return {'group_id':group_id,'response_sha256':fingerprint,'group':canonical,
            'operation':'SET' if operation==SET else 'ADJUST','quantity_name':primary,
            'reason':reason_code,'contract_version':CONTRACT_VERSION}



client=InventoryShopify()
