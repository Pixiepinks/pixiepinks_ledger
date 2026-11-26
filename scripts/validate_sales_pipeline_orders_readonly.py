"""Railway: python -m scripts.validate_sales_pipeline_orders_readonly [--details]."""
import argparse,json
from sqlalchemy import text
from database import SessionLocal
from sales_pipeline_diagnostics import order_reconciliation

def main():
    args=argparse.ArgumentParser();args.add_argument('--details',action='store_true');options=args.parse_args()
    with SessionLocal() as db:
        if db.bind.dialect.name!='postgresql':raise RuntimeError('PostgreSQL READ ONLY required')
        db.execute(text('SET TRANSACTION READ ONLY'));db.execute(text("SET LOCAL statement_timeout='30s'"))
        result={'status':'READ_ONLY_CHECK_COMPLETE','transaction_read_only':db.execute(text('SHOW transaction_read_only')).scalar()=='on','migration':db.execute(text('SELECT version_num FROM alembic_version')).scalar(),'orders':order_reconciliation(db,details=options.details)}
        print(json.dumps(result,default=str,indent=2));db.rollback()
if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'status':'READ_ONLY_CHECK_UNAVAILABLE','mutations':False}));raise SystemExit(1)
