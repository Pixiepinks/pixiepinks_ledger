"""Schema-backed barcode reads and gated barcode-only updates; live writes blocked.

Introspection proves observed schema shape, not collection completeness, search
coverage, custom BarcodeInput semantics or safe update behavior. None of these
observations can enable writes without a verified official contract and complete remote-state
inspection. Legacy `barcode` alone never establishes empty remote barcode state.
"""
import hashlib
import json
import httpx
import re
import math
import time
import retail_barcode_contract as contract
from settings import settings
from shopify_catalog_service import ShopifyCatalogClient

BARCODE_API_VERSION='2026-10'  # Independent of the CRM's global API version.
_TYPE='kind name ofType { kind name ofType { kind name ofType { kind name } } }'
_FIELDS='name type { '+_TYPE+' }'
_INPUT_FIELDS=_FIELDS+' defaultValue'
PROBE='''query RetailBarcodeCapabilities {
 shop { myshopifyDomain }
 currentAppInstallation { accessScopes { handle } }
 variant: __type(name:"ProductVariant") {
  kind name fields(includeDeprecated:true) { '''+_FIELDS+''' args { '''+_INPUT_FIELDS+''' } }
 }
 input: __type(name:"ProductVariantsBulkInput") {
  kind name inputFields { '''+_INPUT_FIELDS+''' }
 }
 barcodeInput: __type(name:"BarcodeInput") {
  kind name inputFields { '''+_INPUT_FIELDS+''' }
 }
 barcodeConnection: __type(name:"ProductVariantBarcodeConnection") { fields(includeDeprecated:true) { ''' +_FIELDS+ ''' } }
 barcodeValue: __type(name:"ProductVariantBarcode") { fields(includeDeprecated:true) { ''' +_FIELDS+ ''' } }
 barcodePageInfo: __type(name:"PageInfo") { fields(includeDeprecated:true) { ''' +_FIELDS+ ''' } }
 barcodeType: __type(name:"BarcodeType") { enumValues(includeDeprecated:true) { name } }
 mutationSchema: __schema { mutationType {
  name fields(includeDeprecated:true) { '''+_FIELDS+''' args { '''+_INPUT_FIELDS+''' } }
 } }
}'''
# Fixed read-only documents; all barcode pages and catalog IDs are exhausted.
PAGE_SIZE=100
MAX_BARCODE_VALUES=10000
MAX_CATALOG_VARIANTS=100000
MAX_READ_REQUESTS=200000
MAX_SCAN_SECONDS=60
READ='''query RetailBarcodeObserved($id:ID!,$after:String) {
 productVariant(id:$id) { id barcode product { id status }
  barcodes(first:100,after:$after) { nodes { value type } pageInfo { hasNextPage endCursor } }
 }
}'''
CATALOG='''query RetailBarcodeCatalog($after:String) {
 productVariants(first:50,after:$after,sortKey:ID) {
  nodes { id barcode product { id status }
   barcodes(first:1) { nodes { value type } pageInfo { hasNextPage endCursor } }
  } pageInfo { hasNextPage endCursor }
 }
}'''
CATALOG_IDS='''query RetailBarcodeCatalogIds($after:String) {
 productVariants(first:100,after:$after,sortKey:ID) { nodes { id } pageInfo { hasNextPage endCursor } }
}'''
MUTATION='''mutation RetailApprovedBarcode($productId:ID!,$variants:[ProductVariantsBulkInput!]!) {
 productVariantsBulkUpdate(productId:$productId,variants:$variants,allowPartialUpdates:false) {
  product { id } productVariants { id } userErrors { code field }
 }
}'''


def mutation_payload(snapshot):
    if not isinstance(snapshot,dict) or not re.fullmatch(r'gid://shopify/Product/[0-9]+',str(snapshot.get('product_id',''))) or not re.fullmatch(r'gid://shopify/ProductVariant/[0-9]+',str(snapshot.get('variant_id',''))):raise RemoteError('INVALID_TARGET_IDENTITY')
    code=snapshot.get('code')
    if not isinstance(code,str) or not re.fullmatch(r'P[0-9A-Z]{1,99}',code):raise RemoteError('INVALID_COMMITTED_INTERNAL_CODE')
    # type is nullable with a null default: omit it; never use a regulated enum.
    return {'productId':snapshot['product_id'],'variants':[{'id':snapshot['variant_id'],'barcodes':[{'value':code}]}]}


class RemoteError(Exception):
    def __init__(self,code='REMOTE_UNAVAILABLE',retry_after=None):
        self.code=code;self.retry_after=retry_after
        super().__init__(code)  # Never retain upstream bodies or tokens.


class BarcodeClient(ShopifyCatalogClient):
    def _read(self,document,variables):
        # Bounded retries apply only to safe reads, never mutations.
        for attempt in range(3):
            try:return self._read_once(document,variables)
            except RemoteError as e:
                if e.code not in {'THROTTLED','REMOTE_UNAVAILABLE'} or attempt==2:raise
                time.sleep(e.retry_after if e.retry_after is not None else 2**attempt)

    def _read_once(self,document,variables):
        if document not in {PROBE,READ,CATALOG,CATALOG_IDS}:raise RemoteError('UNSUPPORTED_DOCUMENT')
        try:
            response=self.http.post(f'{self.base_url}/admin/api/{BARCODE_API_VERSION}/graphql.json',
                headers={'X-Shopify-Access-Token':self.get_token()},
                json={'query':document,'variables':variables},timeout=httpx.Timeout(15,connect=5))
            if response.status_code==429:
                try:delay=float(response.headers.get('Retry-After','2'))
                except (ValueError,TypeError):raise RemoteError('RATE_LIMIT_RETRY_LATER') from None
                if not math.isfinite(delay) or not 0<=delay<=30:raise RemoteError('RATE_LIMIT_RETRY_LATER')
                raise RemoteError('THROTTLED',retry_after=delay)
            if response.status_code==401:
                self.clear_token();raise RemoteError('AUTHENTICATION_UNVERIFIED')
            if response.status_code!=200:raise RemoteError('REMOTE_UNAVAILABLE')
            payload=response.json()
            if isinstance(payload,dict) and isinstance(payload.get('errors'),list) and any(isinstance(e,dict) and (e.get('extensions') or {}).get('code')=='THROTTLED' for e in payload['errors']):raise RemoteError('THROTTLED')
            if not isinstance(payload,dict) or payload.get('errors') or not isinstance(payload.get('data'),dict):raise RemoteError('SCHEMA_OR_ACCESS_UNVERIFIED')
            version=response.headers.get('X-Shopify-API-Version','')
            if not version:raise RemoteError('API_VERSION_UNVERIFIED')
            if version!=BARCODE_API_VERSION:raise RemoteError('API_VERSION_FALLBACK_REJECTED')
            return payload['data'],version
        except RemoteError:raise
        except Exception:raise RemoteError() from None

    @staticmethod
    def _fields(value,key):
        fields=value.get(key) if isinstance(value,dict) else None
        if not isinstance(fields,list):return {}
        return {f['name']:f for f in fields if isinstance(f,dict) and isinstance(f.get('name'),str)}

    def capabilities(self):
        result={'supported':False,'scope':False,'version':'',
            'requested_version':BARCODE_API_VERSION,'global_version':settings.SHOPIFY_API_VERSION,
            'contract_verified':False}
        if not self.configured():return {**result,'reason':'SHOPIFY_NOT_CONFIGURED'}
        try:
            data,version=self._read(PROBE,{})
            result['version']=version
            actual=(data.get('shop') or {}).get('myshopifyDomain')
            if not isinstance(actual,str) or actual.lower()!=self.base_url.removeprefix('https://').lower():
                return {**result,'reason':'SHOP_IDENTITY_UNVERIFIED'}
            result['shop_identity_verified']=True
            installation=data.get('currentAppInstallation') or {}
            scopes=installation.get('accessScopes')
            observed={s.get('handle') for s in scopes if isinstance(s,dict) and isinstance(s.get('handle'),str)} if isinstance(scopes,list) else set()
            result['scope']='write_products' in observed
            result['read_products_scope_observed']='read_products' in observed
            result['write_products_scope_observed']='write_products' in observed
            variant=self._fields(data.get('variant'),'fields')
            bulk=self._fields(data.get('input'),'inputFields')
            barcode=self._fields(data.get('barcodeInput'),'inputFields')
            mutation=(data.get('mutationSchema') or {}).get('mutationType')
            mutations=self._fields(mutation,'fields')
            update=mutations.get('productVariantsBulkUpdate')
            result.update(multiple_barcode_field='barcodes' in variant,
                multiple_barcode_input='barcodes' in bulk,
                barcode_input_observed=bool(barcode),bulk_update_observed=bool(update),
                allow_partial_updates_observed='allowPartialUpdates' in self._fields(update,'args'))
            # Only type metadata is hashed. Never persist credentials, products,
            # scopes, domain strings or raw error bodies in this evidence marker.
            schema={k:data.get(k) for k in ('variant','input','barcodeInput','mutationSchema')}
            result['read_contract_verified']=contract.shape_verified(data)
            result['read_supported']=result['read_contract_verified']
            result['supported']=result['read_supported'] and result['scope'] and contract.MUTATION_SEMANTICS_VERIFIED
            result['contract_verified']=result['supported']
            result['schema_fingerprint']=hashlib.sha256(json.dumps(schema,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if not result['scope']:reason='WRITE_PRODUCTS_SCOPE_UNVERIFIED'
            elif not result['multiple_barcode_field']:reason='COMPLETE_BARCODE_STATE_UNAVAILABLE'
            elif not result['multiple_barcode_input'] or not result['bulk_update_observed']:reason='BULK_BARCODE_SCHEMA_UNAVAILABLE'
            elif not result['barcode_input_observed']:reason='BARCODE_INPUT_SCHEMA_UNAVAILABLE'
            else:reason='MULTI_BARCODE_CONTRACT_UNVERIFIED'
            if result['read_supported']:reason='SUPPORTED' if result['supported'] else 'MUTATION_SEMANTICS_UNVERIFIED'
            return {**result,'reason':reason}
        except RemoteError as error:return {**result,'scope':False,'reason':error.code}
        except Exception:return {**result,'scope':False,'reason':'SCHEMA_OR_ACCESS_UNVERIFIED'}

    @staticmethod
    def _check_budget(budget):
        if budget[0]>MAX_READ_REQUESTS:raise RemoteError('CATALOG_READ_BUDGET_EXCEEDED')
        if time.monotonic()>budget[1]:raise RemoteError('CATALOG_SCAN_DEADLINE_EXCEEDED')

    def _barcode_pages(self,variant_id,budget,initial=None):
        after=None;seen_cursors=set();codes=[];values=set();identity=None
        while True:
            from_catalog=initial is not None
            if from_catalog:row=initial;initial=None
            else:
                budget[0]+=1;self._check_budget(budget)
                data,_=self._read(READ,{'id':variant_id,'after':after})
                self._check_budget(budget);row=data.get('productVariant')
            if not isinstance(row,dict) or row.get('id')!=variant_id or 'barcode' not in row:raise RemoteError('REMOTE_VARIANT_UNAVAILABLE')
            product=row.get('product')
            if not isinstance(product,dict) or not re.fullmatch(r'gid://shopify/Product/[0-9]+',str(product.get('id',''))) or product.get('status') not in {'ACTIVE','ARCHIVED','DRAFT'}:raise RemoteError('INVALID_REMOTE_IDENTITY')
            current=(row['id'],product['id'],product['status'],row.get('barcode'))
            if identity is not None and current!=identity:raise RemoteError('REMOTE_CHANGED_DURING_PAGINATION')
            identity=current
            connection=row.get('barcodes');nodes,page=self._connection(connection,limit=1 if from_catalog else PAGE_SIZE)
            for node in nodes:
                if not isinstance(node,dict) or 'type' not in node or not isinstance(node.get('value'),str) or not 1<=len(node['value'])<=500 or node.get('type') not in contract.TYPES|{None}:raise RemoteError('INVALID_BARCODE_STATE')
                if node['value'] in values:raise RemoteError('DUPLICATE_REMOTE_BARCODE')
                values.add(node['value']);codes.append({'value':node['value'],'type':node.get('type')})
                if len(codes)>MAX_BARCODE_VALUES:raise RemoteError('BARCODE_READ_BUDGET_EXCEEDED')
            if not page['hasNextPage']:break
            after=self._next_cursor(page,seen_cursors)
        if identity[3] is not None and not isinstance(identity[3],str):raise RemoteError('INVALID_LEGACY_BARCODE')
        if identity[3] and identity[3] not in values:raise RemoteError('LEGACY_COLLECTION_CONTRADICTION')
        return {'variant_id':identity[0],'product_id':identity[1],'status':identity[2],
            'legacy_barcode':identity[3],'barcodes':codes,'codes':[c['value'] for c in codes]}

    @staticmethod
    def _connection(connection,limit=PAGE_SIZE):
        if not isinstance(connection,dict) or not isinstance(connection.get('nodes'),list) or len(connection['nodes'])>limit:raise RemoteError('INVALID_CONNECTION')
        page=connection.get('pageInfo')
        if not isinstance(page,dict) or 'endCursor' not in page or type(page.get('hasNextPage')) is not bool:raise RemoteError('INCOMPLETE_PAGE_INFO')
        if page['hasNextPage'] and not connection['nodes']:raise RemoteError('EMPTY_CONTINUING_PAGE')
        return connection['nodes'],page

    @staticmethod
    def _next_cursor(page,seen):
        cursor=page.get('endCursor')
        if not isinstance(cursor,str) or not cursor or len(cursor)>2048 or cursor in seen:raise RemoteError('INVALID_PAGINATION_CURSOR')
        seen.add(cursor);return cursor

    def _catalog_ids(self,budget):
        ids=[];seen=set();cursors=set();after=None
        while True:
            budget[0]+=1
            self._check_budget(budget)
            data,_=self._read(CATALOG_IDS,{'after':after});self._check_budget(budget)
            nodes,page=self._connection(data.get('productVariants'))
            for node in nodes:
                value=node.get('id') if isinstance(node,dict) else None
                if not isinstance(value,str) or not re.fullmatch(r'gid://shopify/ProductVariant/[0-9]+',value) or value in seen:raise RemoteError('INVALID_CATALOG_IDENTITY')
                seen.add(value);ids.append(value)
                if len(ids)>min(MAX_CATALOG_VARIANTS,settings.RETAIL_MAX_VARIANTS):raise RemoteError('CATALOG_VARIANT_BUDGET_EXCEEDED')
            if not page['hasNextPage']:return ids
            after=self._next_cursor(page,cursors)

    def inspect(self,snapshot):
        mutation_payload(snapshot)  # Validate exact IDs/internal value without writing.
        cap=self.capabilities()
        if not cap.get('read_supported'):raise RemoteError('COMPLETE_BARCODE_SCHEMA_UNVERIFIED')
        if not (cap.get('read_products_scope_observed') or cap.get('scope')):raise RemoteError('READ_PRODUCTS_SCOPE_UNVERIFIED')
        budget=[0,time.monotonic()+MAX_SCAN_SECONDS];ids=[];seen=set();collisions=[];target=None
        after=None;cursors=set()
        while True:
            budget[0]+=1;self._check_budget(budget)
            data,_=self._read(CATALOG,{'after':after});self._check_budget(budget)
            nodes,page=self._connection(data.get('productVariants'),limit=50)
            for node in nodes:
                variant_id=node.get('id') if isinstance(node,dict) else None
                if not isinstance(variant_id,str) or not re.fullmatch(r'gid://shopify/ProductVariant/[0-9]+',variant_id) or variant_id in seen:raise RemoteError('INVALID_CATALOG_IDENTITY')
                ids.append(variant_id);seen.add(variant_id)
                if len(ids)>min(MAX_CATALOG_VARIANTS,settings.RETAIL_MAX_VARIANTS):raise RemoteError('CATALOG_VARIANT_BUDGET_EXCEEDED')
                value=self._barcode_pages(variant_id,budget,initial=node)
                if any(v.strip().casefold()==snapshot['code'].casefold() for v in value['codes']):collisions.append(variant_id)
                if variant_id==snapshot['variant_id']:target=value
            if not page['hasNextPage']:break
            after=self._next_cursor(page,cursors)
        if target is None:raise RemoteError('REMOTE_VARIANT_UNAVAILABLE')
        if self._catalog_ids(budget)!=ids:raise RemoteError('CATALOG_CHANGED_DURING_SCAN')
        fresh=self._barcode_pages(snapshot['variant_id'],budget)
        if fresh!=target:raise RemoteError('TARGET_CHANGED_DURING_SCAN')
        if fresh['product_id']!=snapshot['product_id']:raise RemoteError('REMOTE_PRODUCT_MISMATCH')
        fresh.update(complete=True,collision_complete=True,collision_ids=collisions,version=BARCODE_API_VERSION,write_scope_confirmed=cap.get('scope') is True,
            readback_untyped_required=True,untyped_local_code=any(c['value']==snapshot['code'] and c['type'] is None for c in fresh['barcodes']))
        return fresh

    def write_empty(self,snapshot):
        # Compile-time release gate: no environment variable or introspection can
        # approve replacement/custom-value semantics. Tests override only with a
        # mocked transport; production remains unconditionally blocked today.
        if not contract.MUTATION_SEMANTICS_VERIFIED:raise RemoteError('MUTATION_SEMANTICS_UNVERIFIED')
        if not (settings.RETAIL_ENABLED and settings.SHOPIFY_PRODUCT_WRITE_ENABLED and settings.RETAIL_BARCODE_SYNC_ENABLED):raise RemoteError('WRITES_DISABLED')
        payload=mutation_payload(snapshot);cap=self.capabilities()
        if not cap.get('supported') or not cap.get('scope'):raise RemoteError('WRITE_CONTRACT_OR_SCOPE_UNVERIFIED')
        state=self.inspect(snapshot)  # Fresh target read is the final network read.
        if not state.get('write_scope_confirmed'):raise RemoteError('WRITE_PRODUCTS_SCOPE_UNVERIFIED')
        if state['status']!='ACTIVE' or state['codes'] or state.get('legacy_barcode') or state['collision_ids']:raise RemoteError('REMOTE_NOT_EMPTY_OR_CODE_REUSED')
        try:
            response=self.http.post(f'{self.base_url}/admin/api/{BARCODE_API_VERSION}/graphql.json',
                headers={'X-Shopify-Access-Token':self.get_token()},json={'query':MUTATION,'variables':payload},timeout=httpx.Timeout(15,connect=5))
            if response.status_code!=200 or response.headers.get('X-Shopify-API-Version')!=BARCODE_API_VERSION:raise RemoteError('MUTATION_OUTCOME_UNCERTAIN')
            body=response.json();result=(body.get('data') or {}).get('productVariantsBulkUpdate')
            if body.get('errors') or not isinstance(result,dict) or not isinstance(result.get('userErrors'),list):raise RemoteError('MUTATION_OUTCOME_UNCERTAIN')
            if result['userErrors']:raise RemoteError('MUTATION_USER_ERRORS_REQUIRES_READBACK')
            if (result.get('product') or {}).get('id')!=snapshot['product_id'] or result.get('productVariants')!=[{'id':snapshot['variant_id']}]:raise RemoteError('MUTATION_IDENTITY_UNCERTAIN')
            # Acceptance is not verification: the worker ALWAYS freshly reads back.
            return {'accepted':True}
        except RemoteError:raise
        except Exception:raise RemoteError('MUTATION_OUTCOME_UNCERTAIN') from None


client=BarcodeClient()
