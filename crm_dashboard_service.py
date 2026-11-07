"""Permission-scoped SQL reporting. No transport or business-state mutations."""
from datetime import datetime,date,time,timedelta,timezone
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from sqlalchemy import select,func,case,literal,cast,String,and_,or_,union_all
from fastapi import HTTPException
from models import (WhatsAppConversationMessage as M,ProcessedWhatsAppMessage as Receipt,WhatsAppConversation as C,
    CRMAnalyticsPhone as Phone,CRMAnalyticsDelivery as Delivery,WhatsAppManualSend as Manual,SavedVoiceSend as Saved,
    WhatsAppMediaSend as Media,WhatsAppMessageAttachment as Attachment,MarketingConsentRequest as ConsentRequest,
    WhatsAppOutboundProductMessage as Product,WhatsAppMessageReferral as Referral,User,ConversationShare,Lead,SalesOrder,SalesFulfillment)
import conversation_access
import inbox_reply_queue
from staff_access import allowed
from settings import settings

TZ=ZoneInfo('Asia/Colombo')
PRESETS={'today':'Today','yesterday':'Yesterday','this_week':'This week','last_week':'Last week','last_7_days':'Last 7 days',
    'this_month':'This month','last_month':'Last month','last_30_days':'Last 30 days','custom':'Custom'}
CATEGORIES={'text':'Text','quick_messages':'Quick Messages','saved_voices':'Saved Voices','recorded_voice':'Recorded voice',
    'products':'Products','images_files':'Images & files','videos':'Videos','consent':'Consent requests','other':'Other / Unknown'}
SOURCES={'all':'All recorded sources','ad':'Ad referral','post':'Post referral','referral':'Any referral','none':'No recorded referral'}
CLOSED=('DELIVERED','LOST','NOT_INTERESTED','INVALID_SPAM')


@dataclass(frozen=True)
class Window:
    start:datetime
    end:datetime
    preset:str
    label:str


def utc(value):return value.astimezone(timezone.utc).replace(tzinfo=None)
def stamp(value):return value.isoformat()+'Z' if value else None


def window(preset='today',date_from='',date_to='',now=None):
    if preset not in PRESETS:raise HTTPException(422,'Choose a supported date period.')
    current=now or datetime.now(timezone.utc)
    if current.tzinfo is None:current=current.replace(tzinfo=timezone.utc)
    current=current.astimezone(TZ);today=current.date()
    midnight=lambda day:datetime.combine(day,time.min,TZ)
    if preset=='custom':
        try:
            start=date.fromisoformat(date_from);end=date.fromisoformat(date_to)
            if start>end:raise ValueError()
        except (ValueError,TypeError):raise HTTPException(422,'Choose valid custom start/end dates.') from None
        try:return Window(utc(midnight(start)),utc(midnight(end+timedelta(days=1))),preset,f'{start} – {end}')
        except (ValueError,OverflowError):raise HTTPException(422,'Custom date boundaries are outside the supported calendar.') from None
    if preset in {'last_7_days','last_30_days'}:
        days=7 if preset=='last_7_days' else 30
        return Window(utc(current)-timedelta(days=days),utc(current),preset,PRESETS[preset])
    start=today;end=current
    if preset=='yesterday':start=today-timedelta(days=1);end=midnight(today)
    elif preset=='this_week':start=today-timedelta(days=today.weekday())
    elif preset=='last_week':end=midnight(today-timedelta(days=today.weekday()));start=end.date()-timedelta(days=7)
    elif preset=='this_month':start=today.replace(day=1)
    elif preset=='last_month':end=midnight(today.replace(day=1));start=(today.replace(day=1)-timedelta(days=1)).replace(day=1)
    return Window(utc(midnight(start)),utc(end),preset,PRESETS[preset])


def valid_provider(column):return and_(column.isnot(None),func.trim(column)!='')


def source_condition(phone,source):
    rm=M.__table__.alias('referral_message')
    refs=select(Referral.id).select_from(Referral.__table__.join(rm,rm.c.id==Referral.message_id)).where(rm.c.phone_number==phone,rm.c.direction=='inbound')
    if source in {'ad','post'}:refs=refs.where(func.lower(Referral.source_type)==source)
    condition=refs.exists()
    return ~condition if source=='none' else condition if source!='all' else literal(True)


class Report:
    def __init__(self,db,user,period,staff_id=None,country='',source='all'):
        self.db,self.user,self.period=db,user,period
        self.staff_id,self.country,self.source=staff_id,country,source
        if source not in SOURCES or (country and (len(country)!=2 or not country.isupper())):raise HTTPException(422,'Invalid country or recorded source filter.')
        if staff_id is not None:
            if user.role!='ADMIN' and staff_id!=user.id:raise HTTPException(403,'Staff cannot select another staff member.')
            target=db.get(User,staff_id)
            if not target or target.role not in {'ADMIN','STAFF','SALES_AGENT','SALES_MANAGER'}:raise HTTPException(422,'Choose an existing staff account.')
        self.whatsapp=allowed(user,'whatsapp')
        self.leads=allowed(user,'leads')
        if not self.whatsapp and (country or source!='all' or staff_id is not None):raise HTTPException(403,'WhatsApp filters require WhatsApp Inbox access.')

    def visibility(self,phone):
        return literal(True) if self.user.role=='ADMIN' else phone.in_(conversation_access.scope(self.db.query(C.phone_number),self.user))

    def filters(self,phone,country_column,contacts=False):
        conditions=[self.visibility(phone),source_condition(phone,self.source)]
        if self.country:conditions.append(func.coalesce(country_column,'ZZ')==self.country)
        if contacts and self.staff_id is not None:
            owned=select(C.phone_number).where(or_(C.assigned_to_user_id==self.staff_id,
                select(ConversationShare.id).where(ConversationShare.conversation_id==C.id,ConversationShare.user_id==self.staff_id).exists()))
            conditions.append(phone.in_(owned))
        return conditions

    def contacts(self):
        return self.db.query(C).outerjoin(Phone,Phone.raw_phone==C.phone_number).filter(*self.filters(C.phone_number,Phone.country,True))

    def events(self):
        """One deduplicated provider message, with receipt/claim-only fallbacks."""
        db=self.db;start,end=self.period.start,self.period.end
        manual=select(Manual.whatsapp_message_id.label('provider'),func.max(Manual.quick_message_id).label('quick'),
            func.max(Manual.sent_by).label('label'),func.max(Manual.analytics_actor_id).label('actor')).where(valid_provider(Manual.whatsapp_message_id)).group_by(Manual.whatsapp_message_id).cte('manual_proof')
        saved=select(Saved.whatsapp_message_id.label('provider'),func.max(Saved.staff_id).label('actor')).where(valid_provider(Saved.whatsapp_message_id)).group_by(Saved.whatsapp_message_id).cte('saved_proof')
        media=select(Media.message_id.label('mid'),func.max(Media.staff_id).label('actor')).group_by(Media.message_id).cte('media_proof')
        attachments=select(Attachment.message_id.label('mid'),func.max(case((Attachment.media_type=='video',1),else_=0)).label('video')).group_by(Attachment.message_id).cte('attachment_proof')
        consent=select(ConsentRequest.whatsapp_message_id.label('provider')).where(valid_provider(ConsentRequest.whatsapp_message_id)).group_by(ConsentRequest.whatsapp_message_id).cte('consent_proof')
        staff_proof=or_(saved.c.actor.isnot(None),media.c.actor.isnot(None),manual.c.provider.isnot(None),and_(M.response_kind.like('staff%'),M.sent_by.isnot(None),M.sent_by!=''))
        actor_kind=func.coalesce(case((M.analytics_actor_kind.in_(['staff','ai','system','automatic']),M.analytics_actor_kind)),case((staff_proof,'staff'),(M.response_kind=='ai','ai'),(M.response_kind.in_(['bicycle','bicycle_product','image_bicycle','product_images','product_image','image_product']),'automatic'),(M.response_kind.in_(['unsupported','handover']),'system'),else_='unknown'))
        category=case((consent.c.provider.isnot(None),'consent'),(manual.c.quick.isnot(None),'quick_messages'),
            (saved.c.actor.isnot(None),'saved_voices'),(M.response_kind=='staff_saved_voice','saved_voices'),
            (M.response_kind=='staff_product','products'),(M.response_kind=='staff_audio','recorded_voice'),
            (attachments.c.video==1,'videos'),(attachments.c.mid.isnot(None),'images_files'),
            (M.analytics_category.in_(list(CATEGORIES)),M.analytics_category),(M.response_kind=='staff','text'),else_='other')
        delivery_status=func.lower(func.coalesce(Delivery.status,M.send_status,'unknown'))
        provider_key=case((valid_provider(M.whatsapp_message_id),literal('provider:')+M.whatsapp_message_id),else_=literal('history:')+cast(M.id,String))
        cols=[provider_key.label('key'),M.direction.label('direction'),M.phone_number.label('phone'),Phone.canonical_phone.label('canonical'),
            Phone.country.label('country'),C.id.label('cid'),M.id.label('mid'),M.created_at.label('at'),
            func.coalesce(M.analytics_actor_id,saved.c.actor,media.c.actor,manual.c.actor).label('actor'),func.coalesce(M.sent_by,manual.c.label,'Unattributed historical').label('label'),
            actor_kind.label('kind'),category.label('category'),delivery_status.label('delivery'),
            case((M.direction=='outbound',1),else_=0).label('accepted'),literal(0).label('failed'),literal('history').label('time_basis')]
        history=select(*cols).select_from(M).outerjoin(Phone,Phone.raw_phone==M.phone_number).outerjoin(C,C.phone_number==M.phone_number).outerjoin(manual,manual.c.provider==M.whatsapp_message_id).outerjoin(saved,saved.c.provider==M.whatsapp_message_id).outerjoin(media,media.c.mid==M.id).outerjoin(attachments,attachments.c.mid==M.id).outerjoin(consent,consent.c.provider==M.whatsapp_message_id).outerjoin(Delivery,Delivery.provider_id==M.whatsapp_message_id).where(
            M.created_at>=start,M.created_at<end,or_(M.direction=='inbound',and_(M.direction=='outbound',valid_provider(M.whatsapp_message_id))),
            *self.filters(M.phone_number,Phone.country))
        # Only receipt IDs absent from history become additional incoming records.
        receipt=select(literal('provider:')+Receipt.message_id,literal('inbound'),Receipt.sender_phone,Phone.canonical_phone,Phone.country,C.id,
            literal(None).cast(M.id.type),Receipt.received_at,literal(None).cast(User.id.type),literal('Customer'),literal('customer'),literal('other'),literal('received'),literal(0),literal(0),literal('receipt')).select_from(Receipt).outerjoin(Phone,Phone.raw_phone==Receipt.sender_phone).outerjoin(C,C.phone_number==Receipt.sender_phone).where(
                Receipt.received_at>=start,Receipt.received_at<end,~select(M.id).where(M.direction=='inbound',M.whatsapp_message_id==Receipt.message_id).exists(),*self.filters(Receipt.sender_phone,Phone.country))
        parts=[history,receipt]
        for table,provider,staff,category_value in [(Manual,Manual.whatsapp_message_id,Manual.analytics_actor_id,'other'),(Saved,Saved.whatsapp_message_id,Saved.staff_id,'saved_voices')]:
            kind='manual' if table is Manual else 'saved'
            cat=case((select(ConsentRequest.id).where(ConsentRequest.whatsapp_message_id==provider).exists(),'consent'),(Manual.quick_message_id.isnot(None),'quick_messages'),
                (select(Product.id).where(Product.whatsapp_message_id==provider).exists(),'products'),else_='other') if table is Manual else literal(category_value)
            label=table.sent_by if table is Manual else select(User.username).where(User.id==staff).scalar_subquery()
            parts.append(select(literal('provider:')+provider,literal('outbound'),C.phone_number,Phone.canonical_phone,Phone.country,C.id,
                literal(None).cast(M.id.type),table.created_at,staff if staff is not None else literal(None).cast(User.id.type),label,literal('staff'),cat,
                func.coalesce(Delivery.status,'unknown'),literal(1),literal(0),literal('attempt')).select_from(table).join(C,C.id==table.conversation_id).outerjoin(Phone,Phone.raw_phone==C.phone_number).outerjoin(Delivery,Delivery.provider_id==provider).where(
                    valid_provider(provider),table.created_at>=start,table.created_at<end,
                    ~select(M.id).where(M.direction=='outbound',M.whatsapp_message_id==provider).exists(),*self.filters(C.phone_number,Phone.country)))
        for table,staff in [(Manual,Manual.analytics_actor_id),(Saved,Saved.staff_id),(Media,Media.staff_id)]:
            label=table.sent_by if table is Manual else select(User.username).where(User.id==staff).scalar_subquery()
            parts.append(select(literal(table.__tablename__+':failed:')+cast(table.id,String),literal('outbound'),C.phone_number,Phone.canonical_phone,Phone.country,C.id,
                literal(None).cast(M.id.type),table.created_at,staff if staff is not None else literal(None).cast(User.id.type),label,literal('staff'),literal('other'),literal('failed_attempt'),literal(0),literal(1),literal('attempt')).select_from(table).join(C,C.id==table.conversation_id).outerjoin(Phone,Phone.raw_phone==C.phone_number).where(
                    table.status.in_(['FAILED','UPLOAD_FAILED','SEND_FAILED']),
                    ~valid_provider(table.whatsapp_message_id) if table is not Media else table.message_id.is_(None),table.created_at>=start,table.created_at<end,*self.filters(C.phone_number,Phone.country)))
        combined=union_all(*parts).cte('activity_sources')
        ranked=select(*combined.c,func.row_number().over(partition_by=(combined.c.direction,combined.c.key),order_by=(combined.c.mid.asc().nulls_last(),combined.c.at.asc())).label('n')).cte('ranked_activity')
        selected=select(*[ranked.c[c.name] for c in combined.c]).where(ranked.c.n==1)
        if self.staff_id is not None:
            selected=selected.where(or_(and_(ranked.c.direction=='outbound',ranked.c.actor==self.staff_id),and_(ranked.c.direction=='inbound',ranked.c.phone.in_(self.contacts().with_entities(C.phone_number)))))
        return selected.cte('activity')

    def first_contacts(self):
        # No report-period predicate: find the first recorded inbound across retained history.
        hist=select(Phone.canonical_phone.label('canonical'),M.created_at.label('at')).select_from(M).join(Phone,Phone.raw_phone==M.phone_number).where(M.direction=='inbound',Phone.canonical_phone.isnot(None))
        rec=select(Phone.canonical_phone.label('canonical'),Receipt.received_at.label('at')).select_from(Receipt).join(Phone,Phone.raw_phone==Receipt.sender_phone).where(Phone.canonical_phone.isnot(None))
        all_inbound=union_all(hist,rec).subquery()
        return select(all_inbound.c.canonical,func.min(all_inbound.c.at).label('first_at')).group_by(all_inbound.c.canonical).cte('first_contact')

    def metrics(self,events):
        e=events.c;first=self.first_contacts();incoming=e.direction=='inbound';staff=and_(e.direction=='outbound',e.kind=='staff');sent=and_(staff,e.accepted==1)
        count=lambda condition:func.coalesce(func.sum(case((condition,1),else_=0)),0)
        fields=[func.count(func.distinct(case((incoming,e.canonical)))).label('unique_contacts'),
            func.count(func.distinct(case((and_(incoming,first.c.first_at>=self.period.start,first.c.first_at<self.period.end),e.canonical)))).label('first_contacts'),
            count(incoming).label('incoming'),count(sent).label('staff_sent'),func.count(func.distinct(case((sent,e.canonical)))).label('contacted'),
            count(and_(staff,e.failed==1)).label('failed_attempts'),count(and_(sent,e.delivery.in_(['delivered','read']))).label('delivered'),
            count(and_(sent,e.delivery=='read')).label('read'),count(and_(sent,e.delivery=='failed')).label('delivery_failed'),
            count(and_(sent,~e.delivery.in_(['delivered','read','failed']))).label('delivery_pending'),count(and_(incoming,e.canonical.is_(None))).label('unresolved_incoming'),count(and_(incoming,e.key.like('history:%'))).label('legacy_incoming'),
            count(and_(sent,e.time_basis=='attempt')).label('claim_only_sends'),count(and_(sent,e.actor.is_(None))).label('unattributed'),count(e.country.is_(None)).label('mapping_missing'),
            count(and_(e.direction=='outbound',e.kind=='ai',e.accepted==1)).label('ai_sent'),count(and_(e.direction=='outbound',e.kind=='system',e.accepted==1)).label('system_sent'),
            count(and_(e.direction=='outbound',e.kind.in_(['unknown','automatic']),e.accepted==1)).label('unknown_sent')]
        fields.extend(count(and_(sent,e.category==key)).label(key) for key in CATEGORIES)
        row=self.db.execute(select(*fields).select_from(events.outerjoin(first,first.c.canonical==e.canonical))).mappings().one()
        result={k:int(v or 0) for k,v in row.items()}
        if result['mapping_missing']:raise HTTPException(503,'Dashboard identity metadata is incomplete. Messages are safe; retry after the reporting backfill is complete.')
        result['returning_contacts']=result['unique_contacts']-result['first_contacts'];return result

    def staff(self,events,offset=0):
        if self.user.role!='ADMIN':return None
        e=events.c;sent=e.accepted==1
        group=case((e.actor.isnot(None),literal('user:')+cast(e.actor,String)),else_=literal('historical:')+func.coalesce(e.label,'Unattributed'))
        total=lambda cond:func.coalesce(func.sum(case((cond,1),else_=0)),0)
        fields=[group.label('key'),func.max(e.actor).label('actor_id'),func.max(e.label).label('label'),
            func.count(func.distinct(case((sent,e.canonical)))).label('customers'),total(sent).label('total'),total(e.failed==1).label('failed')]
        fields.extend(total(and_(sent,e.category==key)).label(key) for key in CATEGORIES)
        grouped=select(*fields).where(e.kind=='staff',e.direction=='outbound').group_by(group).subquery()
        total_rows=self.db.execute(select(func.count()).select_from(grouped)).scalar_one()
        rows=[dict(row) for row in self.db.execute(select(grouped).order_by(grouped.c.total.desc(),grouped.c.key).offset(offset).limit(50)).mappings()]
        ids=[r['actor_id'] for r in rows if r['actor_id'] is not None]
        users={u.id:u for u in self.db.query(User).filter(User.id.in_(ids)).all()} if ids else {}
        for row in rows:
            actor=users.get(row['actor_id']);row['name']=actor.username if actor else ('Former staff · '+(row['label'] or 'Unattributed') if row['actor_id'] else 'Historical · '+(row['label'] or 'Unattributed'))
            row['active']=actor.is_active if actor else False
        return {'rows':rows,'total':total_rows,'offset':offset}

    def operational(self,queue='needs_reply',offset=0,limit=5):
        if queue not in {'needs_reply','unassigned','followups','payment','dispatch'}:raise HTTPException(422,'Choose an operational queue.')
        counts={};items=[];today=datetime.now(TZ).date();db=self.db;can_chat=self.whatsapp
        contacts=self.contacts();pending,_=inbox_reply_queue.summary(db)
        waiting=contacts.join(pending,pending.c.phone==C.phone_number)
        counts['needs_reply']=waiting.count() if can_chat else None
        counts['unassigned']=contacts.filter(C.assigned_to_user_id.is_(None)).count() if can_chat else None
        lead_query=db.query(Lead).filter(Lead.status.notin_(CLOSED),Lead.next_followup.isnot(None),Lead.next_followup<=today)
        lead_query=lead_query.filter(Lead.whatsapp_conversation_id.in_(contacts.with_entities(C.id)))
        if self.user.role=='ADMIN' and self.staff_id is None and not self.country and self.source=='all':
            lead_query=db.query(Lead).filter(Lead.status.notin_(CLOSED),Lead.next_followup.isnot(None),Lead.next_followup<=today)
        counts['followups']=lead_query.count() if self.leads else None
        orders=db.query(SalesOrder).filter(SalesOrder.crm_conversation_id.in_(contacts.with_entities(C.id)))
        can_orders=allowed(self.user,'sales_orders') and settings.SALES_ORDERS_ENABLED
        can_dispatch=allowed(self.user,'fulfillments') and settings.SALES_FULFILLMENT_ENABLED
        payments=orders.filter(SalesOrder.order_status=='PAYMENT_VERIFICATION')
        ready=orders.join(SalesFulfillment,SalesFulfillment.sales_order_id==SalesOrder.id).filter(SalesFulfillment.status=='READY_TO_DISPATCH').distinct()
        counts['payment']=payments.count() if can_orders else None
        counts['dispatch']=ready.distinct().count() if can_orders and can_dispatch else None
        if queue in {'needs_reply','unassigned'} and can_chat:
            query=waiting.add_columns(pending.c.waiting_since) if queue=='needs_reply' else contacts.filter(C.assigned_to_user_id.is_(None)).add_columns(literal(None).cast(C.created_at.type))
            query=query.order_by(pending.c.waiting_since.desc(),C.last_message_at.desc()) if queue=='needs_reply' else query.order_by(C.last_message_at.desc())
            rows=query.offset(offset).limit(limit).all();users=conversation_access.owners(db,[r[0] for r in rows])
            for c,since in rows:items.append({'name':c.customer_name or 'WhatsApp customer','reason':'Customer waiting for a reply' if queue=='needs_reply' else 'No assigned owner',
                'waiting_since':stamp(since),'owner':users[c.assigned_to_user_id].username if c.assigned_to_user_id in users else 'Unassigned','url':f'/crm/whatsapp?conversation={c.id}'})
        elif queue=='followups' and self.leads:
            # Today first, then recent overdue work; ancient follow-ups remain in the full list.
            for lead in lead_query.order_by(case((Lead.next_followup==today,0),else_=1),Lead.next_followup.desc(),Lead.id.desc()).offset(offset).limit(limit):
                items.append({'name':lead.customer_name,'reason':'Follow-up due '+str(lead.next_followup),'owner':lead.assigned_to or 'Unassigned','waiting_since':None,
                    'url':f'/crm/whatsapp?conversation={lead.whatsapp_conversation_id}' if lead.whatsapp_conversation_id and can_chat else (f'/crm/leads/{lead.id}' if self.user.role=='ADMIN' else None)})
        elif queue in {'payment','dispatch'} and can_orders and (queue!='dispatch' or can_dispatch):
            for order in (payments if queue=='payment' else ready).order_by(SalesOrder.updated_at.desc()).offset(offset).limit(limit):
                items.append({'name':order.customer_name,'reason':'Payment verification required' if queue=='payment' else 'Ready to dispatch','owner':'Order team','waiting_since':None,
                    'url':f'/crm/sales-orders/{order.id}' if self.user.role=='ADMIN' else f'/crm/whatsapp?conversation={order.crm_conversation_id}' if can_chat else None})
        return {'counts':counts,'items':items,'selected':queue,'current':True}

    def pipeline(self,statuses):
        if not self.leads:return None
        rows=self.pipeline_query().with_entities(Lead.status,func.count(Lead.id)).group_by(Lead.status).all()
        order={status:i for i,status in enumerate(statuses)}
        return [{'status':status,'count':int(count)} for status,count in sorted(rows,key=lambda row:(order.get(row[0],len(order)),row[0]))]

    def pipeline_query(self):
        q=self.db.query(Lead).filter(Lead.created_at>=self.period.start,Lead.created_at<self.period.end)
        if self.user.role!='ADMIN' or self.staff_id is not None or self.country or self.source!='all':q=q.filter(Lead.whatsapp_conversation_id.in_(self.contacts().with_entities(C.id)))
        return q

    def details(self,mode,events,offset=0,category='',actor_id=None,lead_status=''):
        if mode=='pipeline':
            if not self.leads:raise HTTPException(403,'CRM / Leads access required.')
            q=self.pipeline_query()
            if lead_status:q=q.filter(Lead.status==lead_status)
            rows=q.order_by(Lead.created_at.desc(),Lead.id.desc()).offset(offset).limit(50).all()
            return {'total':q.count(),'rows':[{'name':r.customer_name,'category':r.status,'at':stamp(r.created_at),'phone':None,
                'url':f'/crm/whatsapp?conversation={r.whatsapp_conversation_id}' if self.whatsapp and r.whatsapp_conversation_id else f'/crm/leads/{r.id}' if self.user.role=='ADMIN' else None} for r in rows]}
        if events is None:raise HTTPException(403,'WhatsApp Inbox access required.')
        e=events.c
        if mode not in {'unique_contacts','first_contacts','incoming','staff_sent','contacted'}:raise HTTPException(422,'Choose a dashboard activity metric.')
        condition=e.direction=='inbound' if mode in {'unique_contacts','first_contacts','incoming'} else and_(e.direction=='outbound',e.kind=='staff',e.accepted==1)
        query=select(events).where(condition)
        if category:
            if category not in CATEGORIES:raise HTTPException(422,'Choose a known message category.')
            query=query.where(e.category==category)
        if actor_id is not None:
            if self.user.role!='ADMIN' and actor_id!=self.user.id:raise HTTPException(403,'Staff cannot select another sender.')
            query=query.where(e.actor==actor_id)
        if mode in {'unique_contacts','first_contacts','contacted'}:
            query=query.where(e.canonical.isnot(None))
            if mode=='first_contacts':
                first=self.first_contacts();query=query.join(first,first.c.canonical==e.canonical).where(first.c.first_at>=self.period.start,first.c.first_at<self.period.end)
            base=query.subquery()
            grouped=select(base.c.canonical.label('phone'),func.min(base.c.cid).label('cid'),func.max(base.c.at).label('at'),func.count().label('messages')).group_by(base.c.canonical).subquery()
            total=self.db.execute(select(func.count()).select_from(grouped)).scalar_one()
            rows=self.db.execute(select(grouped,C.customer_name).outerjoin(C,C.id==grouped.c.cid).order_by(grouped.c.at.desc(),grouped.c.phone).offset(offset).limit(50)).mappings()
            return {'total':total,'rows':[{'name':r['customer_name'] or 'WhatsApp contact','phone':r['phone'],'at':stamp(r['at']),
                'category':str(r['messages'])+' messages','url':f"/crm/whatsapp?conversation={r['cid']}" if r['cid'] else None} for r in rows]}
        base=query.subquery()
        total=self.db.execute(select(func.count()).select_from(base)).scalar_one()
        rows=self.db.execute(select(base,C.customer_name).outerjoin(C,C.id==base.c.cid).order_by(base.c.at.desc(),base.c.key).offset(offset).limit(50)).mappings()
        return {'total':total,'rows':[{'name':r['customer_name'] or 'WhatsApp contact','phone':r['canonical'] or 'Unresolved number','at':stamp(r['at']),
            'category':CATEGORIES.get(r['category'],'Other / Unknown')+' · '+(r['label'] or 'Unattributed') if mode=='staff_sent' else 'Incoming customer message',
            'url':f"/crm/whatsapp?conversation={r['cid']}" if r['cid'] else None} for r in rows]}
