"""Transactional local outbox; never executes a Shopify or financial operation."""
from datetime import datetime
from sqlalchemy import event,inspect,select
from sqlalchemy.orm import Session
from models import SalesOrder,SalesPayment,Customer
from loyalty_models import LoyaltyRecalculation,LoyaltyConfig

@event.listens_for(Session,'before_flush')
def collect(session,context,instances):
    if session.info.get('loyalty_processing'):return
    objects=[r for r in session.new|session.dirty if isinstance(r,(SalesOrder,SalesPayment,Customer))]
    if objects:session.info.setdefault('loyalty_objects',[]).extend(objects)

@event.listens_for(Session,'after_flush_postexec')
def queue(session,context):
    objects=session.info.pop('loyalty_objects',[])
    if not objects or session.info.get('loyalty_processing'):return
    connection=session.connection()
    if not inspect(connection).has_table(LoyaltyRecalculation.__tablename__):return
    if not connection.execute(select(LoyaltyConfig.id).where(LoyaltyConfig.id==1)).first():return
    ids=set()
    for obj in objects:
        if isinstance(obj,Customer):ids.add(obj.id)
        else:
            cid=obj.customer_id if isinstance(obj,SalesOrder) else connection.execute(select(SalesOrder.customer_id).where(SalesOrder.id==obj.sales_order_id)).scalar()
            if cid:ids.add(cid)
    if not ids:return
    if connection.dialect.name=='postgresql':from sqlalchemy.dialects.postgresql import insert
    elif connection.dialect.name=='sqlite':from sqlalchemy.dialects.sqlite import insert
    else:return
    for cid in ids:
        statement=insert(LoyaltyRecalculation).values(customer_id=cid,status='PENDING',attempts=0,updated_at=datetime.utcnow())
        connection.execute(statement.on_conflict_do_update(index_elements=['customer_id'],set_={'status':'PENDING','attempts':0,'error':None,'updated_at':datetime.utcnow()}))

@event.listens_for(Session,'after_rollback')
def rollback(session):session.info.pop('loyalty_objects',None)


def process_one(sessions):
    import loyalty_service as svc
    cid=None
    try:
        with sessions() as db:
            if not svc.config(db):return False
            job=db.query(LoyaltyRecalculation).filter_by(status='PENDING').order_by(LoyaltyRecalculation.updated_at).with_for_update(skip_locked=True).first()
            if not job:return False
            cid=job.customer_id;db.info['loyalty_processing']=True
            mapping=[r.order_id for r in db.query(svc.LoyaltyOrderIdentity).filter_by(customer_id=cid)]
            for order in db.query(SalesOrder).filter((SalesOrder.customer_id==cid)|(SalesOrder.id.in_(mapping or [-1]))).order_by(SalesOrder.id):svc.sync_redemptions(db,order)
            svc.refresh_member(db,cid);db.delete(job);db.commit();return True
    except Exception:
        if cid is not None:
            with sessions() as db:
                row=db.query(LoyaltyRecalculation).filter_by(customer_id=cid).with_for_update().one_or_none()
                if row:
                    row.attempts+=1;row.error='LOCAL_RECALCULATION_FAILED'
                    if row.attempts>=5:row.status='REVIEW'
                    db.commit()
        # The caller logs a fixed message; no customer/order payload or exception text.
        return False
