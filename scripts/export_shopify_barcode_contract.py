"""Read-only schema evidence for an approved existing Railway runtime.

Run in the application's working directory:
  python -m scripts.export_shopify_barcode_contract

This file can also be provided as reviewed stdin to an approved remote Python
session; it imports only the existing ShopifyCatalogClient/settings modules.
It never queries product records, accepts credentials/hostnames, changes flags,
mutates, reads SQL or writes files. Exit 2 always means writes remain blocked.
"""
import argparse
import json
import re
import httpx
from settings import settings
from shopify_catalog_service import ShopifyCatalogClient

VERSION='2026-10'
MAX_TYPES=12
TYPE_REF='kind name ofType { kind name ofType { kind name ofType { kind name } } }'
FIELD='name type { '+TYPE_REF+' }'
INPUT_FIELD=FIELD+' defaultValue'
TYPE_BODY='''kind name fields(includeDeprecated:true) { '''+FIELD+''' args { '''+INPUT_FIELD+''' } }
 inputFields { '''+INPUT_FIELD+''' } enumValues(includeDeprecated:true) { name }
 possibleTypes { kind name }'''
PROBE='''query RetailBarcodeContractEvidence {
 shop { myshopifyDomain }
 currentAppInstallation { accessScopes { handle } }
 variant: __type(name:"ProductVariant") { '''+TYPE_BODY+''' }
 bulkInput: __type(name:"ProductVariantsBulkInput") { '''+TYPE_BODY+''' }
 barcodeInput: __type(name:"BarcodeInput") { '''+TYPE_BODY+''' }
 mutationType: __schema { mutationType { fields { '''+FIELD+''' args { '''+INPUT_FIELD+''' } } } }
}'''
RELATED='query RetailBarcodeRelatedType($name:String!) { type: __type(name:$name) { '+TYPE_BODY+' } }'


class EvidenceError(Exception):
    pass


class EvidenceReader:
    def __init__(self,client):self.client=client

    def read(self,query,variables):
        if query not in {PROBE,RELATED}:raise EvidenceError('UNSUPPORTED_READ_DOCUMENT')
        try:
            r=self.client.http.post(f'{self.client.base_url}/admin/api/{VERSION}/graphql.json',
                headers={'X-Shopify-Access-Token':self.client.get_token()},
                json={'query':query,'variables':variables},timeout=httpx.Timeout(15,connect=5))
            if r.status_code==429:raise EvidenceError('RATE_LIMITED_EVIDENCE_INCOMPLETE')
            if r.status_code!=200:raise EvidenceError('READ_ONLY_REQUEST_FAILED')
            if r.headers.get('X-Shopify-API-Version')!=VERSION:raise EvidenceError('SERVED_API_VERSION_UNVERIFIED')
            data=r.json()
            if not isinstance(data,dict) or data.get('errors') or not isinstance(data.get('data'),dict):raise EvidenceError('SCHEMA_ACCESS_UNVERIFIED')
            return data['data']
        except EvidenceError:raise
        except Exception:raise EvidenceError('READ_ONLY_REQUEST_UNAVAILABLE') from None


def fields(value,key):
    result=value.get(key) if isinstance(value,dict) else None
    if not isinstance(result,list):return {}
    return {f['name']:f for f in result if isinstance(f,dict) and isinstance(f.get('name'),str)}


def metadata(value):
    """Whitelist introspection keys: no arbitrary response/error/config payloads."""
    if isinstance(value,list):return [metadata(v) for v in value]
    if isinstance(value,dict):return {k:metadata(v) for k,v in value.items() if k in {'name','kind','ofType','type','args','fields','inputFields','enumValues','possibleTypes','defaultValue'}}
    if value is None or isinstance(value,(str,bool)):return value
    raise EvidenceError('INVALID_SCHEMA_METADATA')


def referenced(value):
    names=set()
    if isinstance(value,list):
        for v in value:names.update(referenced(v))
    elif isinstance(value,dict):
        if value.get('kind') in {'OBJECT','INTERFACE','INPUT_OBJECT','ENUM','UNION'}:
            name=value.get('name')
            if isinstance(name,str) and re.fullmatch(r'[_A-Za-z][_0-9A-Za-z]{0,127}',name):names.add(name)
        for v in value.values():names.update(referenced(v))
    return names


def export_evidence(client=None,config=None):
    config=config or settings
    result={'status':'BLOCKED','read_only':True,'requested_api_version':VERSION,'served_api_version':None,
        'global_api_version':config.SHOPIFY_API_VERSION,'shop_identity_verified':False,
        'scope_handles_observed':[],'schema_metadata':{},'related_types':{},
        'contract_verified':False,'complete_barcode_state_verified':False,
        'catalog_collision_coverage_verified':False,'pilot_variant':None}
    missing=[n for n in ('SHOPIFY_SHOP','SHOPIFY_CLIENT_ID','SHOPIFY_CLIENT_SECRET') if not getattr(config,n,None)]
    if missing:return {**result,'reason':'RAILWAY_RUNTIME_CONNECTION_UNAVAILABLE','missing_configuration':missing}
    if config.SHOPIFY_SHOP.strip().lower() in {'pixiepinks-barcode-lab','pixiepinks-barcode-lab.myshopify.com'}:
        return {**result,'reason':'LAB_CONNECTION_NOT_PERMITTED'}
    client=client or ShopifyCatalogClient()
    try:
        reader=EvidenceReader(client);data=reader.read(PROBE,{})
        actual=(data.get('shop') or {}).get('myshopifyDomain')
        if not isinstance(actual,str) or actual.lower()!=client.base_url.removeprefix('https://').lower():raise EvidenceError('SHOP_IDENTITY_UNVERIFIED')
        result.update(shop_identity_verified=True,verified_shop_domain=actual,served_api_version=VERSION)
        scopes=(data.get('currentAppInstallation') or {}).get('accessScopes')
        if not isinstance(scopes,list):raise EvidenceError('SCOPE_EVIDENCE_INCOMPLETE')
        result['scope_handles_observed']=sorted({s['handle'] for s in scopes if isinstance(s,dict) and s.get('handle') in {'read_products','write_products'}})
        variant=fields(data.get('variant'),'fields');bulk=fields(data.get('bulkInput'),'inputFields')
        mutation=fields((data.get('mutationType') or {}).get('mutationType'),'fields')
        root={'variant_barcodes':variant.get('barcodes'),'variant_legacy_barcode':variant.get('barcode'),
            'variant_id':variant.get('id'),'bulk_variant_id':bulk.get('id'),
            'bulk_barcodes':bulk.get('barcodes'),'bulk_legacy_barcode':bulk.get('barcode'),
            'bulk_input':data.get('bulkInput'),'barcode_input':data.get('barcodeInput'),
            'bulk_update':mutation.get('productVariantsBulkUpdate')}
        result['schema_metadata']={k:metadata(v) for k,v in root.items()}
        if any(root[k] is None for k in ('variant_barcodes','bulk_barcodes','barcode_input','bulk_update')):
            raise EvidenceError('REQUIRED_BARCODE_SCHEMA_UNAVAILABLE')
        # Follow named return/input/enum types discovered in actual metadata.
        # Never invent BarcodeInput fields, collection nodes, enum values or inputs.
        seen={'ProductVariant','Product','Query','Mutation','ProductVariantsBulkInput','BarcodeInput'}
        pending=referenced({k:v for k,v in root.items() if k!='bulk_input'})-seen
        # Preserve all bulk input signatures/defaults for required-field review,
        # but do not traverse unrelated inventory/price/metafield input graphs.
        while pending:
            if len(result['related_types'])>=MAX_TYPES:raise EvidenceError('SCHEMA_TYPE_BUDGET_REACHED')
            name=sorted(pending)[0];pending.remove(name);seen.add(name)
            value=reader.read(RELATED,{'name':name}).get('type')
            if not isinstance(value,dict) or value.get('name')!=name:raise EvidenceError('RELATED_SCHEMA_TYPE_UNVERIFIED')
            clean=metadata(value);result['related_types'][name]=clean
            pending.update(referenced(clean)-seen)
        result['reason']='SCHEMA_EVIDENCE_REQUIRES_REVIEW'
        # This exports metadata, not a compatibility certification. No product
        # record/collection or complete collision scan has been performed.
        return result
    except EvidenceError as e:return {**result,'reason':str(e)}
    except Exception:return {**result,'reason':'SCHEMA_EVIDENCE_INCOMPLETE'}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(export_evidence(),indent=2,sort_keys=True))
    return 2


if __name__=='__main__':raise SystemExit(main())
