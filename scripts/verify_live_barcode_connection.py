"""Read-only readiness evidence from existing authorized app settings only.

Run: python -m scripts.verify_live_barcode_connection
Exit 2 means blocked. No credentials, hostname overrides, mutation, flags, scopes,
Railway changes, database access or pilot execution are supported by this command.
"""
import argparse
import json
from settings import settings
from retail_barcode_shopify import BarcodeClient,BARCODE_API_VERSION


def verify_connection(client=None,config=None):
    config=config or settings
    result={'status':'BLOCKED','read_only':True,'shop_source':'existing_application_configuration',
        'adapter_api_requested':BARCODE_API_VERSION,'global_api_version':config.SHOPIFY_API_VERSION,
        'shop_identity_verified':False,'verified_shop_domain':None,'served_api_version':None,
        'read_products_scope_observed':False,'write_products_scope_observed':False,
        'complete_barcode_state_verified':False,'catalog_collision_coverage_verified':False,
        'variant_identity_matches_verified':False,'pilot_variant':None}
    missing=[name for name in ('SHOPIFY_SHOP','SHOPIFY_CLIENT_ID','SHOPIFY_CLIENT_SECRET') if not getattr(config,name,None)]
    if missing:return {**result,'reason':'LIVE_CONNECTION_NOT_CONFIGURED','missing_configuration':missing}
    if config.SHOPIFY_SHOP.strip().lower() in {'pixiepinks-barcode-lab','pixiepinks-barcode-lab.myshopify.com'}:
        return {**result,'reason':'LAB_CONNECTION_NOT_PERMITTED'}
    client=client or BarcodeClient()
    try:
        capabilities=client.capabilities()
        # Whitelist fields; never print an arbitrary client/upstream payload.
        verified=capabilities.get('shop_identity_verified') is True
        result['shop_identity_verified']=verified
        if verified:result['verified_shop_domain']=client.base_url.removeprefix('https://')
        result['served_api_version']=capabilities.get('version') or None
        result['read_products_scope_observed']=capabilities.get('read_products_scope_observed') is True
        result['write_products_scope_observed']=capabilities.get('write_products_scope_observed') is True
        result['schema_fingerprint']=capabilities.get('schema_fingerprint')
        known={'SHOPIFY_NOT_CONFIGURED','SHOP_IDENTITY_UNVERIFIED','WRITE_PRODUCTS_SCOPE_UNVERIFIED',
            'COMPLETE_BARCODE_STATE_UNAVAILABLE','BULK_BARCODE_SCHEMA_UNAVAILABLE',
            'BARCODE_INPUT_SCHEMA_UNAVAILABLE','MULTI_BARCODE_CONTRACT_UNVERIFIED',
            'API_VERSION_UNVERIFIED','API_VERSION_FALLBACK_REJECTED',
            'AUTHENTICATION_UNVERIFIED','THROTTLED','REMOTE_UNAVAILABLE','SCHEMA_OR_ACCESS_UNVERIFIED'}
        result['adapter_reason']=capabilities.get('reason') if capabilities.get('reason') in known else 'UNVERIFIED'
        # Introspection cannot certify collections, collisions or a safe mutation.
        result['reason']='COMPLETE_BARCODE_CONTRACT_UNVERIFIED'
        if not verified:result['reason']='SHOP_IDENTITY_UNVERIFIED'
        elif not result['read_products_scope_observed'] or not result['write_products_scope_observed']:
            result['reason']='REQUIRED_SCOPE_EVIDENCE_INCOMPLETE'
        if not client.configured():result['reason']='LIVE_CONNECTION_NOT_CONFIGURED'
        return result
    except Exception:return {**result,'reason':'READ_ONLY_CONNECTION_CHECK_UNAVAILABLE'}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(verify_connection(),sort_keys=True,indent=2))
    return 2  # This diagnostic cannot unlock synchronization or approve a pilot.


if __name__=='__main__':raise SystemExit(main())
