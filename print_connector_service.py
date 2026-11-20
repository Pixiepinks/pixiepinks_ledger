"""Receipt-only encrypted tunnel; legacy native TLS/proxy guards are unchanged.
The Windows peer verifies public HTTPS and pins this app's independent public key.
The backend NEVER infers TLS from forwarding headers or fabricates scope schemes.
"""
import base64,time
from datetime import timedelta
from fastapi import HTTPException
from settings import settings
from hardware_models import PrintDevice,PrintConnectorNonce,HardwareEvent
from windows_print_agent import envelope as wire
from print_agent_security import public_host
import hardware_service as h


def public_configuration():
    value={'enabled':settings.PRINT_CONNECTOR_ENCRYPTED_ENABLED,'origin':settings.PRINT_AGENT_PUBLIC_ORIGIN,'public_key':None,'protocol':wire.VERSION}
    try:value['public_key']=wire.public_hex(settings.PRINT_CONNECTOR_PRIVATE_KEY)
    except (ValueError,TypeError):pass
    return value


def consume_nonce(db,ctx):
    from crm_analytics_tracking import insert_for
    conn=db.connection()
    if conn.dialect.name=='sqlite' and not conn.connection.driver_connection.in_transaction:conn.exec_driver_sql('BEGIN IMMEDIATE')
    result=db.execute(insert_for(conn,PrintConnectorNonce.__table__).values(id=ctx.request_id,expires_at=h.now()+timedelta(minutes=3)).on_conflict_do_nothing(index_elements=['id']))
    if result.rowcount!=1:
        db.rollback();raise HTTPException(409,'Encrypted request already consumed; never replay')
    for row in db.query(PrintConnectorNonce).filter(PrintConnectorNonce.expires_at<h.now()).limit(100):db.delete(row)
    db.commit()  # Fence survives a lost response/handler rollback across processes.


def invoke(db,message):
    if not isinstance(message,dict) or set(message)!={'timestamp','origin','method','credential','body'}:raise HTTPException(422,'Invalid connector message')
    if type(message['timestamp']) is not int or abs(time.time()-message['timestamp'])>60:raise HTTPException(401,'Expired connector message')
    try:
        if public_host(message['origin'])!=public_host(settings.PRINT_AGENT_PUBLIC_ORIGIN):raise ValueError()
    except (ValueError,TypeError,AttributeError):raise HTTPException(403,'Connector origin mismatch') from None
    method=message['method'];body=message['body']
    if not isinstance(body,dict):raise HTTPException(422,'Connector body required')
    if method=='pair':
        if set(body)!={'code'} or not isinstance(body['code'],str) or len(body['code'])>150:raise HTTPException(422,'Pairing unavailable')
        try:did,_=body['code'].split('.',1)
        except ValueError:raise HTTPException(401,'Pairing unavailable') from None
        d=db.query(PrintDevice).filter_by(id=did,shop=h.retail.shop()).one_or_none()
        if not d or not d.config.get('receipt_only'):raise HTTPException(403,'Receipt-only enrollment required')
        h.throttle(db,'connector-pair:'+did,12)
        return {'token':h.pair(db,body['code']),'protocol':h.VERSION}
    d=h.authenticate(db,'Bearer '+message['credential'] if isinstance(message['credential'],str) else '')
    if d.shop!=h.retail.shop() or not d.config.get('receipt_only'):raise HTTPException(403,'Receipt-only active device required')
    h.throttle(db,'connector-device:'+d.id,240)
    # throttle commits; reacquire device lock and revalidate activation/version.
    d=h.authenticate(db,'Bearer '+message['credential'])
    if method=='config' and not body:return h.public_device(d)
    if method=='heartbeat':return h.heartbeat(db,d,body)
    if method=='claim' and not body:
        job=h.claim(db,d)
        if job and (job['kind'] not in {'RECEIPT','TEST'} or job['queue']!=h.RECEIPT_QUEUE):raise HTTPException(409,'Forbidden connector job')
        return {'job':job}
    required={'pdf':{'id','claim'},'permit':{'id','claim'},'progress':{'id','claim'},'result':{'id','claim','status','spool_id'}}
    if method not in required or set(body)!=required[method]:raise HTTPException(422,'Unsupported receipt connector operation')
    if method=='result':
        job=h.claimed(db,d,body['id'],body['claim'])
        if job.kind not in {'RECEIPT','TEST'} or job.queue!=h.RECEIPT_QUEUE:raise HTTPException(403,'Receipt-only outcome required')
        return h.acknowledge(db,d,body['id'],body['claim'],body['status'],body['spool_id'])
    job=h.permit_job(db,d,body['id'],body['claim'])
    if method=='pdf':
        if not job.pdf or len(job.pdf)>wire.MAX_RECEIPT:raise HTTPException(409,'Receipt exceeds encrypted transport budget; manual review required')
        return {'pdf':base64.b64encode(job.pdf).decode()}
    if method=='permit':
        if job.permitted_at:raise HTTPException(409,'Dispatch permit already consumed; never replay')
        job.permitted_at=h.now();h.event(db,d,'DISPATCH_PERMIT',j=job);db.commit()
        return {'authorized':True,'valid_seconds':5}
    if not job.permitted_at:raise HTTPException(409,'No dispatch permit')
    job.delivered_at=h.now();d.last_seen=h.now();db.commit();return {'continue':True}


def handle(db,packet):
    if not settings.PRINT_CONNECTOR_ENCRYPTED_ENABLED or not settings.POS_LOCAL_PRINT_ENABLED:raise HTTPException(404,'Receipt connector disabled')
    try:message,ctx=wire.open_request(settings.PRINT_CONNECTOR_PRIVATE_KEY,packet)
    except Exception:raise HTTPException(401,'Encrypted connector authentication unavailable') from None
    try:
        # Freshness checked before consuming; duplicate fencing is DB-backed.
        if not isinstance(message,dict) or type(message.get('timestamp')) is not int or abs(time.time()-message['timestamp'])>60:raise HTTPException(401,'Expired connector message')
        consume_nonce(db,ctx)
        value=invoke(db,message);result={'status':200,'value':value}
    except HTTPException as exc:
        db.rollback();result={'status':exc.status_code,'value':{'detail':exc.detail}}
    except Exception:
        db.rollback();result={'status':503,'value':{'detail':'Connector operation unavailable; inspect durable job history before retry'}}
    return wire.seal_response(ctx,result)
