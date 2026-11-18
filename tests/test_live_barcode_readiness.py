"""Readiness command must not invent a connection, mutate or disclose secrets."""
from types import SimpleNamespace
from scripts.verify_live_barcode_connection import verify_connection


class Client:
    base_url='https://configured-live-fixture.myshopify.com'
    def __init__(self):self.calls=0
    def configured(self):return True
    def capabilities(self):
        self.calls+=1
        return {'shop_identity_verified':True,'version':'2026-10',
            'read_products_scope_observed':True,'write_products_scope_observed':True,
            'schema_fingerprint':'f'*64,'raw_secret':'NEVER_PRINT'}
    def write_empty(self,*args):raise AssertionError('Readiness must never write')


def config(**kw):return SimpleNamespace(**{'SHOPIFY_SHOP':'configured-live-fixture','SHOPIFY_CLIENT_ID':'NEVER_PRINT_ID','SHOPIFY_CLIENT_SECRET':'NEVER_PRINT_SECRET','SHOPIFY_API_VERSION':'2026-07',**kw})


def test_missing_configuration_is_reported_without_http_or_values():
    client=Client();result=verify_connection(client,config(SHOPIFY_SHOP=None,SHOPIFY_CLIENT_ID=None,SHOPIFY_CLIENT_SECRET=None))
    assert client.calls==0 and result['reason']=='LIVE_CONNECTION_NOT_CONFIGURED'
    assert result['missing_configuration']==['SHOPIFY_SHOP','SHOPIFY_CLIENT_ID','SHOPIFY_CLIENT_SECRET']
    assert result['pilot_variant'] is None and result['verified_shop_domain'] is None


def test_lab_connection_is_never_used():
    for value in ['pixiepinks-barcode-lab','PIXIEPINKS-BARCODE-LAB.MYSHOPIFY.COM']:
        client=Client();result=verify_connection(client,config(SHOPIFY_SHOP=value))
        assert client.calls==0 and result['reason']=='LAB_CONNECTION_NOT_PERMITTED'


def test_readonly_metadata_never_claims_complete_eligibility_or_creates_pilot():
    client=Client();result=verify_connection(client,config())
    assert client.calls==1 and result['read_only'] and result['status']=='BLOCKED'
    assert result['verified_shop_domain']=='configured-live-fixture.myshopify.com'
    assert result['served_api_version']=='2026-10' and result['global_api_version']=='2026-07'
    assert result['read_products_scope_observed'] and result['write_products_scope_observed']
    assert not result['complete_barcode_state_verified'] and not result['catalog_collision_coverage_verified']
    assert result['pilot_variant'] is None and 'NEVER_PRINT' not in str(result)


def test_error_payload_is_not_printed():
    client=Client()
    def fail():raise RuntimeError('NEVER_PRINT_SECRET')
    client.capabilities=fail
    result=verify_connection(client,config())
    assert result['reason']=='READ_ONLY_CONNECTION_CHECK_UNAVAILABLE' and 'NEVER_PRINT' not in str(result)
