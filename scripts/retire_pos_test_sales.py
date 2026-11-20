"""Default read-only preview; production apply requires separately approved exact plan.
No Shopify mutation methods are available to this command.
"""
import argparse,json
from fastapi import HTTPException
from database import SessionLocal
from models import User
from scripts.inspect_pos_sales_readonly import ReadOnlyClient,read_only_session
import pos_test_cleanup as cleanup


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-id',type=int,required=True)
    parser.add_argument('--apply-approved-plan')
    parser.add_argument('--confirm')
    parser.add_argument('--notes')
    args=parser.parse_args()
    try:
        client=ReadOnlyClient()
        if args.apply_approved_plan:
            with SessionLocal() as db:
                cleanup.stop(db.bind.dialect.name=='postgresql','Production apply requires PostgreSQL worker locking')
                with db.begin():
                    result=cleanup.apply(db,db.get(User,args.admin_id),client,args.apply_approved_plan,args.confirm,args.notes)
        else:
            with read_only_session(SessionLocal) as db:
                result=cleanup.plan(db,db.get(User,args.admin_id),client)
    except HTTPException as exc:
        print(json.dumps({'status':'BLOCKED','reason':exc.detail,'production_cleanup_executed':False}));return 1
    except Exception:
        print(json.dumps({'status':'BLOCKED','reason':'Read/search/schema/transaction unavailable; no exception payload exported','production_cleanup_verified':False}));return 1
    print(json.dumps(result,indent=2,sort_keys=True));return 0

if __name__=='__main__':raise SystemExit(main())
