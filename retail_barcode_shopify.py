"""Fail-closed Shopify barcode adapter. No speculative multi-barcode mutation.

The production adapter intentionally cannot write until a versioned complete-slot
read, cross-catalog collision query and non-destructive CUSTOM input contract have
been independently validated. Legacy `barcode` alone never proves emptiness.
The durable workflow consumes this interface; fixture adapters exercise dispatch.
"""
import httpx
from settings import settings
from shopify_catalog_service import ShopifyCatalogClient

PROBE='''query RetailBarcodeCapabilities {
 currentAppInstallation { accessScopes { handle } }
 variant: __type(name:"ProductVariant") { fields { name } }
 input: __type(name:"ProductVariantsBulkInput") { inputFields { name } }
}'''
READ='''query RetailBarcodeObserved($id:ID!) {
 productVariant(id:$id) { id barcode product { id status } }
}'''


class RemoteError(Exception):
    def __init__(self,code='REMOTE_UNAVAILABLE'):
        self.code=code
        super().__init__(code)  # Never retain upstream bodies or tokens.


class BarcodeClient(ShopifyCatalogClient):
    def _read(self,document,variables):
        # Read documents are internal constants; no caller-supplied GraphQL.
        if document not in {PROBE,READ}:raise RemoteError('UNSUPPORTED_DOCUMENT')
        try:
            response=self.http.post(f'{self.base_url}/admin/api/{settings.SHOPIFY_API_VERSION}/graphql.json',
                headers={'X-Shopify-Access-Token':self.get_token()},
                json={'query':document,'variables':variables},timeout=httpx.Timeout(15,connect=5))
            if response.status_code==429:raise RemoteError('THROTTLED')
            if response.status_code!=200:raise RemoteError('REMOTE_UNAVAILABLE')
            payload=response.json()
            if payload.get('errors') or not isinstance(payload.get('data'),dict):raise RemoteError('SCHEMA_OR_ACCESS_UNVERIFIED')
            version=response.headers.get('X-Shopify-API-Version','')
            if not version:raise RemoteError('API_VERSION_UNVERIFIED')
            return payload['data'],version
        except RemoteError:raise
        except Exception:raise RemoteError() from None

    def capabilities(self):
        if not self.configured():return {'supported':False,'scope':False,'reason':'SHOPIFY_NOT_CONFIGURED','version':''}
        data,version=self._read(PROBE,{})
        installation=data.get('currentAppInstallation') or {}
        scopes=installation.get('accessScopes')
        scope=isinstance(scopes,list) and any(isinstance(s,dict) and s.get('handle')=='write_products' for s in scopes)
        fields=[f.get('name') for f in (data.get('variant') or {}).get('fields',[])]
        inputs=[f.get('name') for f in (data.get('input') or {}).get('inputFields',[])]
        return {'supported':False,'scope':scope,'version':version,'requested_version':settings.SHOPIFY_API_VERSION,
            'reason':'MULTI_BARCODE_CONTRACT_UNVERIFIED' if 'barcodes' in fields else 'COMPLETE_BARCODE_STATE_UNAVAILABLE',
            'multiple_barcode_field': 'barcodes' in fields,'multiple_barcode_input':'barcodes' in inputs}

    def inspect(self,snapshot):
        data,version=self._read(READ,{'id':snapshot['variant_id']})
        variant=data.get('productVariant')
        return {'complete':False,'version':version,'variant_id':variant.get('id') if variant else None,
            'product_id':(variant.get('product') or {}).get('id') if variant else None,
            'status':(variant.get('product') or {}).get('status') if variant else None,
            'legacy_barcode':variant.get('barcode') if variant else None,'codes':[],
            'reason':'COMPLETE_BARCODE_STATE_UNAVAILABLE'}

    def write_empty(self,snapshot):
        # Even direct invocation cannot bypass the unsupported-schema gate.
        raise RemoteError('MULTI_BARCODE_CONTRACT_UNVERIFIED')


client=BarcodeClient()
