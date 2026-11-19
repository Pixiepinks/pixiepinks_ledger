"""Credential-safe Railway/operator diagnostic. Queries and read-only DB access only.

python -m scripts.validate_inventory_readonly
python -m scripts.validate_inventory_readonly --variant-id <CRM-ID> --location <LOCATION-GID>
No flags, scopes, inventory or database records are modified.
"""
import argparse
import hashlib
import json
from pathlib import Path
from sqlalchemy import text
from database import SessionLocal
from retail_models import RetailVariant
import inventory_shopify as adapter
import retail_service
from retail_inventory import InventoryError
from settings import settings


class ReadOnlyClient(adapter.InventoryShopify):
    def request(self,query,variables,mutation=False):
        if mutation or query not in {adapter.CONTRACT,adapter.LOCATIONS,adapter.READ}:
            raise InventoryError('DIAGNOSTIC_MUTATION_FORBIDDEN')
        return super().request(query,variables,mutation=False)
    def apply(self,*args,**kwargs):raise InventoryError('DIAGNOSTIC_MUTATION_FORBIDDEN')


def run(client,variant_id=None,location=None,include_contract=False,previous_scopes=None):
    result={'mode':'READ_ONLY','write_authorized':False,'api_version':adapter.API_VERSION,
        'adapter_sha256':hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest(),
        'inventory_write_setting_enabled':bool(settings.SHOPIFY_INVENTORY_WRITE_ENABLED),
        'mutation_contract_verified':adapter.MUTATION_CONTRACT_VERIFIED}
    try:
        result['capabilities']=client.capabilities(include_contract=True) if include_contract else client.capabilities()
        scopes=result['capabilities'].get('installed_scope_handles',[])
        result['preserved_scope_check']={'checked':previous_scopes is not None,'missing_previous_scopes':sorted(set(previous_scopes or [])-set(scopes))}
        result['production_write_ready']=bool(result['capabilities'].get('production_write_ready')) and not result['preserved_scope_check']['missing_previous_scopes']
        result['contract_version']=adapter.CONTRACT_VERSION
        result['locations']=client.locations()
        if variant_id is not None:
            if not adapter.gid(location,'Location'):raise InventoryError('LOCATION_REQUIRED')
            with SessionLocal() as db:
                if db.bind.dialect.name=='postgresql':db.execute(text('SET TRANSACTION READ ONLY'))
                # No workflow imports, create_all, migrations, model updates or commits.
                row=db.query(RetailVariant).filter_by(shop=retail_service.shop(),id=variant_id).one_or_none()
                if row is None:raise InventoryError('VARIANT_NOT_FOUND')
                line={'variant_id':row.variant_id,'product_id':row.product_id,'inventory_item_id':row.inventory_item_id,'location_id':location}
                if row.missing or row.product_status!='ACTIVE':raise InventoryError('VARIANT_INACTIVE')
                result['observation']=client.observe(line)
                result['identity']=line
                db.rollback()
        result['status']='READ_ONLY_COMPLETE'
    except InventoryError as error:
        result['status']='READ_ONLY_UNAVAILABLE';result['reason']=error.reason
    except Exception:
        result['status']='READ_ONLY_UNAVAILABLE';result['reason']='DIAGNOSTIC_UNAVAILABLE'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--variant-id',type=int);parser.add_argument('--location')
    parser.add_argument('--include-contract',action='store_true',help='Include public schema metadata, never credentials')
    parser.add_argument('--previous-scopes',help='Earlier diagnostic JSON to verify all installed scopes were preserved')
    parser.add_argument('--require-write-ready',action='store_true',help='Fail if any release compatibility/scope check remains blocked; never authorize writes')
    args=parser.parse_args()
    previous=None
    if args.previous_scopes:
        try:
            previous=json.loads(Path(args.previous_scopes).read_text())['capabilities']['installed_scope_handles']
            if not isinstance(previous,list) or not all(isinstance(v,str) for v in previous):raise ValueError()
        except (OSError,ValueError,KeyError,TypeError):parser.error('Previous scope evidence must be a diagnostic JSON with installed_scope_handles')
    result=run(ReadOnlyClient(),args.variant_id,args.location,args.include_contract,previous)
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if result['status']=='READ_ONLY_COMPLETE' and (not args.require_write_ready or result['production_write_ready']) else 1


if __name__=='__main__':raise SystemExit(main())
