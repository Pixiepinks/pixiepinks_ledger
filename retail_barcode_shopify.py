"""Explicitly versioned, read-only barcode diagnostics; writes remain fail-closed.

Introspection proves observed schema shape, not collection completeness, search
coverage, custom BarcodeInput semantics or safe update behavior. None of these
observations can enable writes without a verified official contract and complete remote-state
inspection. Legacy `barcode` alone never establishes empty remote barcode state.
"""
import hashlib
import json
import httpx
from settings import settings
from shopify_catalog_service import ShopifyCatalogClient

BARCODE_API_VERSION='2026-10'  # Independent of the CRM's global API version.
_TYPE='kind name ofType { kind name ofType { kind name ofType { kind name } } }'
_FIELDS='name type { '+_TYPE+' }'
PROBE='''query RetailBarcodeCapabilities {
 shop { myshopifyDomain }
 currentAppInstallation { accessScopes { handle } }
 variant: __type(name:"ProductVariant") {
  kind name fields { '''+_FIELDS+''' args { '''+_FIELDS+''' } }
 }
 input: __type(name:"ProductVariantsBulkInput") {
  kind name inputFields { '''+_FIELDS+''' }
 }
 barcodeInput: __type(name:"BarcodeInput") {
  kind name inputFields { '''+_FIELDS+''' }
 }
 mutationSchema: __schema { mutationType {
  name fields { '''+_FIELDS+''' args { '''+_FIELDS+''' } }
 } }
}'''
# This is diagnostic evidence only. It deliberately never returns complete=True.
# If the legacy field is removed, GraphQL errors fail closed rather than falling
# back to another API version or treating the variant as empty.
READ='''query RetailBarcodeObserved($id:ID!) {
 productVariant(id:$id) { id barcode product { id status } }
}'''


class RemoteError(Exception):
    def __init__(self,code='REMOTE_UNAVAILABLE'):
        self.code=code
        super().__init__(code)  # Never retain upstream bodies or tokens.


class BarcodeClient(ShopifyCatalogClient):
    def _read(self,document,variables):
        if document not in {PROBE,READ}:raise RemoteError('UNSUPPORTED_DOCUMENT')
        try:
            response=self.http.post(f'{self.base_url}/admin/api/{BARCODE_API_VERSION}/graphql.json',
                headers={'X-Shopify-Access-Token':self.get_token()},
                json={'query':document,'variables':variables},timeout=httpx.Timeout(15,connect=5))
            if response.status_code==429:raise RemoteError('THROTTLED')
            if response.status_code==401:
                self.clear_token();raise RemoteError('AUTHENTICATION_UNVERIFIED')
            if response.status_code!=200:raise RemoteError('REMOTE_UNAVAILABLE')
            payload=response.json()
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
            result['schema_fingerprint']=hashlib.sha256(json.dumps(schema,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if not result['scope']:reason='WRITE_PRODUCTS_SCOPE_UNVERIFIED'
            elif not result['multiple_barcode_field']:reason='COMPLETE_BARCODE_STATE_UNAVAILABLE'
            elif not result['multiple_barcode_input'] or not result['bulk_update_observed']:reason='BULK_BARCODE_SCHEMA_UNAVAILABLE'
            elif not result['barcode_input_observed']:reason='BARCODE_INPUT_SCHEMA_UNAVAILABLE'
            else:reason='MULTI_BARCODE_CONTRACT_UNVERIFIED'
            return {**result,'reason':reason}
        except RemoteError as error:return {**result,'scope':False,'reason':error.code}
        except Exception:return {**result,'scope':False,'reason':'SCHEMA_OR_ACCESS_UNVERIFIED'}

    def inspect(self,snapshot):
        data,version=self._read(READ,{'id':snapshot['variant_id']})
        variant=data.get('productVariant')
        return {'complete':False,'version':version,'variant_id':variant.get('id') if variant else None,
            'product_id':(variant.get('product') or {}).get('id') if variant else None,
            'status':(variant.get('product') or {}).get('status') if variant else None,
            'legacy_barcode':variant.get('barcode') if variant else None,'codes':[],
            'reason':'COMPLETE_BARCODE_STATE_UNAVAILABLE'}

    def write_empty(self,snapshot):
        # Introspection, credentials, flags or direct invocation never bypass the
        # official-contract and complete-state verification blockers. No mutation is sent.
        raise RemoteError('MULTI_BARCODE_CONTRACT_UNVERIFIED')


client=BarcodeClient()
