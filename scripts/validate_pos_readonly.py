"""Sanitized operator checks; no migrations, database writes or Shopify mutations.

python -m scripts.validate_pos_readonly --variant-id <CRM-ID> --location <SHOPIFY-LOCATION-GID>
Run only inside the existing authorized server environment. Never paste credentials.
"""
import argparse
import hashlib
import json
from pathlib import Path
from sqlalchemy import text
from database import SessionLocal
from retail_models import RetailVariant
import pos_shopify as adapter
import inventory_shopify
import retail_service
from settings import settings


class ReadOnlyClient(adapter.POSShopify):
    def request(self,query,variables):
        if query in {adapter.CREATE,adapter.REFUND}:raise adapter.POSRemoteError('Diagnostic mutations forbidden')
        return super().request(query,variables)
    def create(self,*args,**kwargs):raise adapter.POSRemoteError('Diagnostic mutations forbidden')
    def refund(self,*args,**kwargs):raise adapter.POSRemoteError('Diagnostic mutations forbidden')


def run(client,variant_id=None,location=None):
    result={'mode':'READ_ONLY','write_authorized':False,'api_version':adapter.VERSION,'contract':adapter.CONTRACT,
        'adapter_sha256':hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest(),
        'flags':{'pos':settings.POS_ENABLED,'checkout':settings.POS_CHECKOUT_WRITE_ENABLED,'refund':settings.POS_REFUND_WRITE_ENABLED},
        'physical_balance':'PROVISIONAL','offline_token_acceptance':'Not proven by schema; operator must confirm existing app uses an offline token'}
    try:
        result['capabilities']=client.capabilities()
        # Null-target queries still validate the entire real read-back selection set.
        client.read('gid://shopify/Order/0');client.find('POS-READ-ONLY-SCHEMA-CHECK')
        result['order_read_queries_verified']=True
        result['locations']=inventory_shopify.client.locations()
        if variant_id is not None:
            if not inventory_shopify.gid(location,'Location'):raise adapter.POSRemoteError('Location required')
            with SessionLocal() as db:
                if db.bind.dialect.name=='postgresql':db.execute(text('SET TRANSACTION READ ONLY'))
                row=db.query(RetailVariant).filter_by(shop=retail_service.shop(),id=variant_id).one_or_none()
                if row is None or row.missing or row.product_status!='ACTIVE':raise adapter.POSRemoteError('Active catalog variant required')
                line={'variant_id':row.variant_id,'product_id':row.product_id,'inventory_item_id':row.inventory_item_id,'location_id':location}
                result['observation']=client.inspect(line);result['identity']=line;db.rollback()
        result['status']='READ_ONLY_COMPLETE'
    except Exception:
        result['status']='READ_ONLY_UNAVAILABLE';result['reason']='Query, schema, scopes, stock or connection unavailable; investigate in authorized runtime without disclosing tokens'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--variant-id',type=int);parser.add_argument('--location');args=parser.parse_args()
    value=run(ReadOnlyClient(),args.variant_id,args.location);print(json.dumps(value,indent=2,sort_keys=True));return 0 if value['status']=='READ_ONLY_COMPLETE' else 1


if __name__=='__main__':raise SystemExit(main())
