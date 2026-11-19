"""Reviewed read-only Railway diagnostic; no activation or database writes.

Run in the existing application working directory, or provide this reviewed file
as stdin to an approved remote Python session. No new deployment is needed for
stdin execution. Credentials remain in that runtime; never export its environment.

  python -m scripts.validate_shopify_barcode_readonly
  python -m scripts.validate_shopify_barcode_readonly --allocation-id EXISTING_ID

Exit 0 means the read-only scan completed, not permission to write. Exit 2 means
blocked/incomplete. No allocation is invented and no write authorization occurs.
"""
import argparse
from collections import Counter
from datetime import datetime,timedelta,timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import httpx
from sqlalchemy import text
from settings import settings
import retail_barcode_contract as contract
import retail_barcode_shopify as adapter
from retail_barcode_shopify import BarcodeClient,RemoteError,PROBE,READ,CATALOG,CATALOG_IDS

EXPECTED_SHOP='eb2b70.myshopify.com'
EXPECTED_ADAPTER_SHA='f976339787ec90bfd7bcba9610b3271fec8393ecc222acc7e1a5f7b669c09334'
READ_DOCUMENTS={PROBE:'schema',READ:'barcode_pages',CATALOG:'catalog',CATALOG_IDS:'verify_ids'}
ERROR_CODES={'THROTTLED','MAX_COST_EXCEEDED','ACCESS_DENIED','GRAPHQL_VALIDATION_FAILED','UNDEFINEDFIELD','UNDEFINED_FIELD'}
SAFE_FAILURES=set(re.findall(r"RemoteError\('([A-Z_]+)'",Path(adapter.__file__).read_text()))|{'READ_ONLY_TRANSPORT_REJECTED','READ_ONLY_DIAGNOSTIC'}


class Metrics:
    def __init__(self):
        self.started=time.monotonic();self.attempts=Counter();self.statuses=Counter();self.errors=Counter()
        self.throttles=0;self.timeouts=0;self.transport_errors=0;self.auth_requests=0;self.blocked_requests=0
        self.request_seconds=0.;self.max_request_seconds=0.;self.cost={};self.minimum_available=None
        self.variants=0;self.values=0;self.secondary=0;self.multi=0;self.empty=0;self.typed=0;self.untyped=0;self.rechecks=0;self._seen=set()
    def observe(self,row):
        if row['variant_id'] in self._seen:self.rechecks+=1;return
        self._seen.add(row['variant_id']);n=len(row['barcodes']);self.variants+=1;self.values+=n
        self.secondary+=max(0,n-1);self.multi+=n>1;self.empty+=n==0
        self.untyped+=sum(v['type'] is None for v in row['barcodes']);self.typed+=sum(v['type'] is not None for v in row['barcodes'])
    def report(self):
        elapsed=max(0,time.monotonic()-self.started)
        return {'elapsed_seconds':round(elapsed,3),'http_attempts':dict(self.attempts),'http_status_counts':dict(self.statuses),
            'graphql_error_codes':dict(self.errors),'throttle_responses':self.throttles,'timeouts':self.timeouts,
            'transport_errors':self.transport_errors,'auth_requests':self.auth_requests,'blocked_requests':self.blocked_requests,
            'request_seconds':round(self.request_seconds,3),'max_request_seconds':round(self.max_request_seconds,3),
            'last_cost':self.cost,'minimum_available_cost':self.minimum_available,
            'fully_examined_variants':self.variants,'barcode_values_examined':self.values,'secondary_values_examined':self.secondary,
            'variants_with_multiple_barcodes':self.multi,'empty_variants':self.empty,'typed_values':self.typed,
            'untyped_values':self.untyped,'target_rechecks':self.rechecks,
            'examined_variants_per_second':round(self.variants/elapsed,3) if elapsed else None}


class ReadOnlyTransport:
    def __init__(self,delegate,metrics):self.delegate=delegate;self.metrics=metrics
    def post(self,url,*args,**kwargs):
        # Only the existing authentication grant or exact deployed READ documents
        # may leave the process. No mutation, REST endpoint or foreign host.
        try:u=urlsplit(str(url));port=u.port
        except Exception:raise RemoteError('READ_ONLY_TRANSPORT_REJECTED') from None
        valid=not args and not any(k in kwargs for k in ('content','files','params')) and u.scheme=='https' and u.hostname==EXPECTED_SHOP and port in {None,443} and not(u.username or u.password or u.query or u.fragment)
        body=kwargs.get('json');data=kwargs.get('data');kind=None
        if valid and u.path==f'/admin/api/{adapter.BARCODE_API_VERSION}/graphql.json' and isinstance(body,dict) and set(body)=={'query','variables'} and isinstance(body['variables'],dict) and isinstance(body.get('query'),str):
            kind=READ_DOCUMENTS.get(body.get('query'))
        auth=valid and u.path=='/admin/oauth/access_token' and isinstance(data,dict) and data.get('grant_type')=='client_credentials' and set(data)<= {'grant_type','client_id','client_secret'} and body is None
        if not kind and not auth:
            self.metrics.blocked_requests+=1;raise RemoteError('READ_ONLY_TRANSPORT_REJECTED')
        if auth:self.metrics.auth_requests+=1
        else:self.metrics.attempts[kind]+=1
        started=time.monotonic()
        try:
            r=self.delegate.post(url,*args,**kwargs)
            if kind:
                self.metrics.statuses[str(r.status_code)]+=1
                try:
                    payload=r.json()
                    if isinstance(payload,dict):
                        for e in payload.get('errors',[]) if isinstance(payload.get('errors'),list) else []:
                            ext=e.get('extensions') if isinstance(e,dict) else None
                            code=str(ext.get('code','')).upper() if isinstance(ext,dict) else ''
                            self.metrics.errors[code if code in ERROR_CODES else 'OTHER']+=1
                            if code=='THROTTLED':self.metrics.throttles+=1
                        cost=(payload.get('extensions') or {}).get('cost')
                        if isinstance(cost,dict):
                            throttle=cost.get('throttleStatus') or {};clean={}
                            for name,value in {**{k:cost.get(k) for k in ('requestedQueryCost','actualQueryCost')},**{k:throttle.get(k) for k in ('currentlyAvailable','maximumAvailable','restoreRate')}}.items():
                                if type(value) in {int,float} and math.isfinite(value) and value>=0:clean[name]=value
                            self.metrics.cost=clean
                            if 'currentlyAvailable' in clean:self.metrics.minimum_available=min(self.metrics.minimum_available,clean['currentlyAvailable']) if self.metrics.minimum_available is not None else clean['currentlyAvailable']
                except Exception:pass  # Raw bodies/errors must never enter output.
                if r.status_code==429:self.metrics.throttles+=1
            return r
        except httpx.TimeoutException:self.metrics.timeouts+=1;raise
        except httpx.RequestError:self.metrics.transport_errors+=1;raise
        finally:
            duration=time.monotonic()-started
            self.metrics.request_seconds+=duration;self.metrics.max_request_seconds=max(self.metrics.max_request_seconds,duration)


class DiagnosticClient(BarcodeClient):
    def __init__(self,http_client=httpx):
        super().__init__(http_client=http_client);self.metrics=Metrics();self.http=ReadOnlyTransport(self.http,self.metrics)
    def write_empty(self,*args,**kwargs):raise RemoteError('READ_ONLY_DIAGNOSTIC')
    def _barcode_pages(self,*args,**kwargs):
        row=super()._barcode_pages(*args,**kwargs);self.metrics.observe(row);return row
    def scan_all_accessible(self):
        # Same deployed queries, parser, identity/cursor/value limits and ID
        # verification as inspect(), without inventing a target or local code.
        cap=self.capabilities()
        if not cap.get('read_supported'):raise RemoteError('COMPLETE_BARCODE_SCHEMA_UNVERIFIED')
        if not(cap.get('read_products_scope_observed') or cap.get('scope')):raise RemoteError('READ_PRODUCTS_SCOPE_UNVERIFIED')
        budget=[0,time.monotonic()+adapter.MAX_SCAN_SECONDS];ids=[];seen=set();after=None;cursors=set()
        while True:
            budget[0]+=1;self._check_budget(budget)
            data,_=self._read(CATALOG,{'after':after});self._check_budget(budget)
            nodes,page=self._connection(data.get('productVariants'),limit=50)
            for node in nodes:
                vid=node.get('id') if isinstance(node,dict) else None
                if not isinstance(vid,str) or not re.fullmatch(r'gid://shopify/ProductVariant/[0-9]+',vid) or vid in seen:raise RemoteError('INVALID_CATALOG_IDENTITY')
                ids.append(vid);seen.add(vid)
                if len(ids)>min(adapter.MAX_CATALOG_VARIANTS,settings.RETAIL_MAX_VARIANTS):raise RemoteError('CATALOG_VARIANT_BUDGET_EXCEEDED')
                self._barcode_pages(vid,budget,initial=node)
            if not page['hasNextPage']:break
            after=self._next_cursor(page,cursors)
        if self._catalog_ids(budget)!=ids:raise RemoteError('CATALOG_CHANGED_DURING_SCAN')
        return {'complete':True,'catalog_membership_verified':True,'collision_complete':False,'candidate_code_checked':False}


def load_allocation(allocation_id,engine=None):
    """No ORM/main import, migrations, DML, locks, commits or audit writes."""
    if engine is None:
        from database import engine
    if engine.dialect.name!='postgresql':return None,'READ_ONLY_POSTGRES_REQUIRED'
    try:
        with engine.connect() as connection:
            transaction=connection.begin()
            try:
                connection.exec_driver_sql('SET TRANSACTION READ ONLY')
                connection.exec_driver_sql("SET LOCAL statement_timeout = '5000ms'")
                rows=connection.execute(text('''SELECT i.id AS ledger_id,i.value AS code,v.id,v.version,v.shop,v.product_id,v.variant_id,v.sku,
                    v.barcode,v.product_status,v.missing,v.stale,v.synced_at
                    FROM retail_identifiers i JOIN retail_variants v ON v.id=i.variant_id
                    WHERE i.id=:id AND i.shop=:shop AND i.kind='barcode'
                    AND v.shop=i.shop AND v.local_barcode=i.value LIMIT 2'''),{'id':allocation_id,'shop':(settings.SHOPIFY_SHOP or '').strip().lower()}).mappings().all()
                if len(rows)!=1:return None,'NO_UNAMBIGUOUS_COMMITTED_ALLOCATION'
                row=dict(rows[0]);synced=row['synced_at']
                if isinstance(synced,datetime) and synced.tzinfo:synced=synced.astimezone(timezone.utc).replace(tzinfo=None)
                if row['product_status']!='ACTIVE' or row['missing'] or row['stale'] or not isinstance(synced,datetime) or synced<datetime.utcnow()-timedelta(days=7) or row['barcode']:
                    return None,'ALLOCATION_NOT_LOCALLY_ELIGIBLE'
                if not re.fullmatch(r'P[0-9A-Z]{1,99}',row['code'] or '') or not re.fullmatch(r'gid://shopify/Product/[0-9]+',row['product_id'] or '') or not re.fullmatch(r'gid://shopify/ProductVariant/[0-9]+',row['variant_id'] or ''):
                    return None,'ALLOCATION_IDENTITY_UNVERIFIED'
                duplicate=connection.execute(text('''SELECT count(*) FROM retail_variants WHERE shop=:shop AND id<>:id AND
                    (lower(trim(barcode))=lower(:code) OR lower(trim(local_barcode))=lower(:code) OR (:sku<>'' AND sku=:sku))'''),
                    {'shop':row['shop'],'id':row['id'],'code':row['code'],'sku':row['sku'] or ''}).scalar_one()
                if duplicate:return None,'LOCAL_IDENTIFIER_REQUIRES_REVIEW'
                return {k:row[k] for k in ('ledger_id','id','version','shop','product_id','variant_id','sku','code')},'COMMITTED_ALLOCATION_READ_ONLY'
            finally:transaction.rollback()
    except Exception:return None,'CRM_READ_ONLY_QUERY_FAILED'


def run_diagnostic(allocation_id=None,client=None,allocation_loader=load_allocation,config=None):
    config=config or settings;started=time.monotonic()
    result={'generated_at_utc':datetime.now(timezone.utc).isoformat(),'status':'BLOCKED','read_only':True,'write_authorized':False,'source_gate_false':contract.MUTATION_SEMANTICS_VERIFIED is False,
        'write_flags_disabled':not(config.SHOPIFY_PRODUCT_WRITE_ENABLED or config.RETAIL_BARCODE_SYNC_ENABLED),
        'expected_shop':EXPECTED_SHOP,'verified_shop':None,'requested_api_version':adapter.BARCODE_API_VERSION,
        'served_api_version':None,'scope_observations':None,'coverage_complete':False,'catalog_membership_verified':False,
        'scope_of_coverage':'All variants returned by unfiltered productVariants under this app visibility',
        'atomic_snapshot':False,'concurrent_edit_risk':True,'candidate':None,'candidate_reason':'NO_ALLOCATION_SELECTED',
        'database_read_requested':allocation_id is not None,'adapter_source_sha256':None,
        'scan_limits':{'seconds':adapter.MAX_SCAN_SECONDS,'logical_reads':adapter.MAX_READ_REQUESTS,
            'variants':min(adapter.MAX_CATALOG_VARIANTS,settings.RETAIL_MAX_VARIANTS),'values_per_variant':adapter.MAX_BARCODE_VALUES}}
    if not result['source_gate_false'] or not result['write_flags_disabled']:return {**result,'reason':'WRITE_GUARDS_NOT_DISABLED'}
    actual_hash=hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest();result['adapter_source_sha256']=actual_hash
    if actual_hash!=EXPECTED_ADAPTER_SHA:return {**result,'reason':'DEPLOYED_SCANNER_VERSION_MISMATCH'}
    missing=[n for n in ('SHOPIFY_SHOP','SHOPIFY_CLIENT_ID','SHOPIFY_CLIENT_SECRET') if not getattr(config,n,None)]
    if missing:return {**result,'reason':'RUNTIME_CONNECTION_UNAVAILABLE','missing_configuration':missing}
    if config.SHOPIFY_SHOP.strip().lower()!='eb2b70':return {**result,'reason':'UNEXPECTED_CONFIGURED_SHOP'}
    client=client or DiagnosticClient();snapshot=None
    try:
        cap=client.capabilities()
        if not cap.get('shop_identity_verified'):
            reason=cap.get('reason')
            return {**result,'reason':reason if reason in SAFE_FAILURES else 'SHOP_IDENTITY_UNVERIFIED','metrics':client.metrics.report()}
        result.update(verified_shop=EXPECTED_SHOP,served_api_version=cap.get('version'),scope_observations={
            'read_products':cap.get('read_products_scope_observed') is True,'write_products':cap.get('write_products_scope_observed') is True},
            barcode_read_schema_verified=cap.get('read_supported') is True)
        if allocation_id is not None:snapshot,result['candidate_reason']=allocation_loader(allocation_id)
        state=client.inspect(snapshot) if snapshot is not None else client.scan_all_accessible()
        result.update(status='READ_ONLY_SCAN_COMPLETE',coverage_complete=state.get('complete') is True,
            catalog_membership_verified=state.get('complete') is True,collision_check_for_candidate=state.get('collision_complete') is True,
            collision_count=len(state.get('collision_ids',[])) if state.get('collision_complete') is True else None,metrics=client.metrics.report())
        if not result['coverage_complete']:
            result.update(status='INCOMPLETE',reason='READ_ONLY_SCAN_INCOMPLETE')
            return result
        if snapshot is not None:
            after,reason=allocation_loader(allocation_id)
            if after!=snapshot:result['candidate_reason']='ALLOCATION_CHANGED_DURING_SCAN'
            elif not state.get('collision_complete') or state['status']!='ACTIVE' or state['codes'] or state.get('legacy_barcode') or state['collision_ids']:result['candidate_reason']='REMOTE_STATE_NOT_ELIGIBLE'
            else:
                result['candidate']={**snapshot,'remote_barcode_values':[],'read_only_conditions_met':True,'administrator_approval_required':True,'write_authorized':False}
                result['candidate_reason']='PROPOSED_ONLY_NOT_AUTHORIZED'
        if not client.metrics.variants:result.update(status='INCONCLUSIVE_EMPTY_CATALOG',coverage_complete=False,reason='EMPTY_ACCESSIBLE_CATALOG_REQUIRES_REVIEW')
        else:result['reason']='SCAN_COMPLETE_WRITE_AUTHORIZATION_NOT_GRANTED'
        if contract.MUTATION_SEMANTICS_VERIFIED is not False or config.SHOPIFY_PRODUCT_WRITE_ENABLED or config.RETAIL_BARCODE_SYNC_ENABLED:
            result.update(status='BLOCKED',reason='WRITE_GUARD_CHANGED',candidate=None)
        result['elapsed_seconds']=round(time.monotonic()-started,3)
        return result
    except RemoteError as error:
        return {**result,'status':'INCOMPLETE','coverage_complete':False,'reason':error.code if error.code in SAFE_FAILURES else 'READ_ONLY_SCAN_FAILED','metrics':client.metrics.report()}
    except Exception:return {**result,'status':'INCOMPLETE','coverage_complete':False,'reason':'READ_ONLY_DIAGNOSTIC_FAILED','metrics':client.metrics.report()}


class SanitizedParser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'Invalid diagnostic arguments. Use --help; never pass credentials.\n')


def allocation_id(value):
    if not re.fullmatch(r'[0-9]{1,10}',value) or not 1<=int(value)<=2147483647:raise argparse.ArgumentTypeError('Invalid allocation ID')
    return int(value)


def main():
    parser=SanitizedParser(description=__doc__);parser.add_argument('--allocation-id',type=allocation_id)
    args=parser.parse_args();report=run_diagnostic(args.allocation_id)
    print(json.dumps(report,indent=2,sort_keys=True))
    return 0 if report['status']=='READ_ONLY_SCAN_COMPLETE' else 2


if __name__=='__main__':raise SystemExit(main())
