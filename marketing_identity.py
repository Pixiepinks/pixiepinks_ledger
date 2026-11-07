"""Consent identity registration; never creates/modifies ordinary CRM contacts."""
import re
from datetime import datetime
import phonenumbers
from sqlalchemy import event, inspect, select, update
from sqlalchemy.orm import Session
from models import Customer,Lead,WhatsAppConversation,MarketingConsent,MarketingConsentEvent
from marketing_consent_constants import SCOPE


def phone(value):
    if not isinstance(value,str) or not re.fullmatch(r'[+0-9 ().-]{3,64}',value.strip()):return None
    digits=re.sub(r'\D','',value)
    if digits.startswith('00'):digits=digits[2:]
    if digits.startswith('0') and len(digits)==10:digits='94'+digits[1:]
    try:
        parsed=phonenumbers.parse('+'+digits,None)
        if not phonenumbers.is_possible_number(parsed):return None
        return phonenumbers.format_number(parsed,phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:return None


def source_fields(row):
    if isinstance(row,Customer):return 'customer_id',row.phone,row.name,'customer'
    if isinstance(row,Lead):return 'lead_id',row.mobile,row.customer_name,'lead'
    return 'conversation_id',row.phone_number,row.customer_name or 'WhatsApp customer','conversation'


def invalidate_identity(connection,previous,reason,conflict=False):
    table=MarketingConsent.__table__;now=datetime.utcnow()
    status='OPTED_OUT' if previous['status']=='OPTED_OUT' else 'NOT_REQUESTED'
    values={'status':status,'affirmative':False,'active_request_id':None,'version':previous['version']+1,'updated_at':now}
    if conflict:values['identity_conflict']=True
    changed=connection.execute(update(table).where(table.c.id==previous['id'],table.c.version==previous['version']).values(**values))
    if changed.rowcount:
        connection.execute(MarketingConsentEvent.__table__.insert().values(consent_id=previous['id'],
            dedup_key=f'identity:{previous["id"]}:{previous["version"]}',event_type='CONSENT_INVALIDATED',
            previous_status=previous['status'],new_status=status,customer_id=previous['customer_id'],
            conversation_id=previous['conversation_id'],phone_e164=previous['phone_e164'],scope=SCOPE,
            actor_name='system:identity',reason=reason,occurred_at=now,recorded_at=now))


def register(connection,row,actor_id=None):
    field,raw,name,kind=source_fields(row);normalized=phone(raw)
    identity='wa:'+normalized if normalized else f'{kind}:{row.id}'
    table=MarketingConsent.__table__;now=datetime.utcnow()
    values={'identity_key':identity,'phone_e164':normalized,field:row.id,'display_name':name[:255],
            'status':'NOT_REQUESTED','scope':SCOPE,'created_by':actor_id,'updated_by':actor_id,'interests':[],'created_at':now,'updated_at':now}
    if connection.dialect.name=='postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    connection.execute(insert(table).values(**values).on_conflict_do_nothing(index_elements=['identity_key']))
    current=connection.execute(select(table).where(table.c.identity_key==identity)).mappings().one()
    # Attach only previously absent links. Different canonical customer/chat IDs
    # sharing a phone are quarantined, never silently merged or transferred.
    if current[field] is None:
        if field!='lead_id' and current['status'] in {'OPTED_IN','PENDING'}:
            invalidate_identity(connection,current,'A new canonical customer/conversation link requires fresh consent verification.')
        connection.execute(update(table).where(table.c.id==current['id'],table.c[field].is_(None)).values({field:row.id}))
    elif current[field]!=row.id and field!='lead_id':
        if not current['identity_conflict']:invalidate_identity(connection,current,'Multiple canonical contacts share this phone; resolve identity before verifying.',conflict=True)
    # A phone change must not resurrect old permission if it is changed back.
    old=connection.execute(select(table).where(table.c[field]==row.id,table.c.identity_key!=identity)).mappings().all()
    for previous in old:
        if previous['status'] in {'OPTED_IN','PENDING'}:
            invalidate_identity(connection,previous,'Canonical phone identity changed; no consent was transferred.')
        # The historical event retains the former ID; current links follow the
        # canonical record without carrying permission to the new phone.
        connection.execute(update(table).where(table.c.id==previous['id'],table.c[field]==row.id).values({field:None}))
    return current['id']


@event.listens_for(Session,'before_flush')
def collect_identities(session,*args):
    rows=[r for r in session.new.union(session.dirty) if isinstance(r,(Customer,Lead,WhatsAppConversation))]
    if not rows:return
    if 'marketing_schema' not in session.info:
        session.info['marketing_schema']=inspect(session.connection()).has_table('marketing_consents')
    if session.info['marketing_schema']:session.info['marketing_identity_rows']=rows


@event.listens_for(Session,'after_flush_postexec')
def register_identities(session,*args):
    for row in session.info.pop('marketing_identity_rows',[]):
        user=session.info.get('inbox_user')
        register(session.connection(),row,user.id if user else None)
