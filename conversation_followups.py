"""One due-work union: explicit conversation schedules override linked lead dates."""
from datetime import datetime,date,time,timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select,func,case,literal,cast,String,union_all
from models import ConversationWorkState as W,WhatsAppConversation as C,Lead,User
import inbox_reply_queue
import conversation_work_service as work


def due(db,contacts,legacy_query,can_chat,can_leads,offset=0,limit=5):
    now=datetime.utcnow();today=datetime.now(ZoneInfo('Asia/Colombo')).date()
    parts=[]
    if can_chat:
        q=contacts.join(W,W.conversation_id==C.id).filter(W.status=='FOLLOW_UP_LATER',W.due_at<=now)
        parts.append(q.with_entities(C.id.label('cid'),literal(None).cast(Lead.id.type).label('lead_id'),C.customer_name.label('name'),
            cast(W.due_at,String).label('due'),literal('conversation').label('kind'),W.followup_user_id.label('owner')).statement)
    if can_leads:
        pending,_=inbox_reply_queue.summary(db)
        suppressed=select(W.conversation_id).where(W.status.in_(['RESOLVED','FOLLOW_UP_LATER']))
        immediate=select(C.id).join(pending,pending.c.phone==C.phone_number)
        legacy=legacy_query.filter((Lead.whatsapp_conversation_id.is_(None)) | (~Lead.whatsapp_conversation_id.in_(suppressed) & ~Lead.whatsapp_conversation_id.in_(immediate)))
        # Multiple old leads attached to one chat yield one reminder, not duplicates.
        key=case((Lead.whatsapp_conversation_id.isnot(None),literal('c:')+cast(Lead.whatsapp_conversation_id,String)),else_=literal('l:')+cast(Lead.id,String))
        chosen=legacy.with_entities(func.min(Lead.id)).group_by(key)
        parts.append(select(Lead.whatsapp_conversation_id.label('cid'),Lead.id.label('lead_id'),Lead.customer_name.label('name'),
            cast(Lead.next_followup,String).label('due'),literal('lead').label('kind'),literal(None).cast(User.id.type).label('owner')).where(Lead.id.in_(chosen)))
    if not parts:return None,[]
    all_due=union_all(*parts).subquery();count=db.execute(select(func.count()).select_from(all_due)).scalar_one()
    rows=db.execute(select(all_due).order_by(case((all_due.c.due>=str(today),0),else_=1),all_due.c.due.desc(),all_due.c.cid,all_due.c.lead_id).offset(offset).limit(limit)).mappings().all()
    owners={u.id:u.username for u in db.query(User).filter(User.id.in_([r['owner'] for r in rows if r['owner']]))}
    result=[]
    for r in rows:
        when=datetime.fromisoformat(r['due']) if r['kind']=='conversation' else None
        label=when.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Asia/Colombo')).strftime('%d %b %Y, %H:%M Sri Lanka') if when else r['due']
        result.append({'name':r['name'],'reason':('Conversation follow-up due ' if when else 'Follow-up due ')+label,
            'due_at':work.iso(when) if when else None,'owner':owners.get(r['owner'],'Unassigned'),
            'waiting_since':None,'conversation_id':r['cid'],'lead_id':r['lead_id']})
    return count,result
