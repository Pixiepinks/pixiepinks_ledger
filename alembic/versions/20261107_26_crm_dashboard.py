"""Reporting identities, delivery chronology and future immutable sender snapshots."""
from datetime import datetime
import re
import phonenumbers
from babel import Locale
from alembic import op
import sqlalchemy as sa
revision='20261107_26'
down_revision='20261106_25'
branch_labels=None
depends_on=None


def tables():
    metadata=sa.MetaData()
    phones=sa.Table('crm_analytics_phones',metadata,sa.Column('raw_phone',sa.String(64),primary_key=True),
        sa.Column('canonical_phone',sa.String(18),index=True),sa.Column('country',sa.String(2),nullable=False,index=True),sa.Column('country_name',sa.String(100),nullable=False))
    delivery=sa.Table('crm_analytics_deliveries',metadata,sa.Column('provider_id',sa.String(255),primary_key=True),
        sa.Column('status',sa.String(16),nullable=False),sa.Column('status_at',sa.DateTime(),nullable=False),sa.Column('rank',sa.Integer(),nullable=False))
    return phones,delivery


def phone_identity(raw):
    normalized=None;region=None
    if isinstance(raw,str) and re.fullmatch(r'[+0-9 ().-]{3,64}',raw.strip()):
        digits=re.sub(r'\D','',raw)
        if digits.startswith('00'):digits=digits[2:]
        if digits.startswith('0') and len(digits)==10:digits='94'+digits[1:]
        try:
            number=phonenumbers.parse('+'+digits,None)
            if phonenumbers.is_possible_number(number):normalized=phonenumbers.format_number(number,phonenumbers.PhoneNumberFormat.E164)
            if phonenumbers.is_valid_number(number):
                candidates=[r for r in phonenumbers.region_codes_for_country_code(1) if phonenumbers.is_valid_number_for_region(number,r)] if number.country_code==1 else [phonenumbers.region_code_for_number(number)]
                if len(candidates)==1 and candidates[0] and re.fullmatch('[A-Z]{2}',candidates[0]):region=candidates[0]
        except phonenumbers.NumberParseException:pass
    return {'raw_phone':raw,'canonical_phone':normalized,'country':region or 'ZZ',
        'country_name':str(Locale.parse('en').territories.get(region,'Unknown numbering region')) if region else 'Unknown numbering region'}


def upgrade():
    bind=op.get_bind();phones,delivery=tables()
    columns={c['name'] for c in sa.inspect(bind).get_columns('whatsapp_conversation_messages')}
    for column in [sa.Column('analytics_actor_id',sa.Integer()),sa.Column('analytics_actor_kind',sa.String(16)),sa.Column('analytics_category',sa.String(24))]:
        if column.name not in columns:op.add_column('whatsapp_conversation_messages',column)
    if 'analytics_actor_id' not in {c['name'] for c in sa.inspect(bind).get_columns('whatsapp_manual_sends')}:op.add_column('whatsapp_manual_sends',sa.Column('analytics_actor_id',sa.Integer()))
    for table in [phones,delivery]:
        table.create(bind,checkfirst=True)
        for index in table.indexes:index.create(bind,checkfirst=True)
    for table,name,fields in [('whatsapp_conversation_messages','ix_wam_analytics_period',['direction','created_at','id']),
        ('whatsapp_conversation_messages','ix_whatsapp_conversation_messages_analytics_actor_id',['analytics_actor_id']),('whatsapp_manual_sends','ix_whatsapp_manual_sends_analytics_actor_id',['analytics_actor_id']),('processed_whatsapp_messages','ix_wap_analytics_received',['received_at','sender_phone'])]:
        if name not in {i['name'] for i in sa.inspect(bind).get_indexes(table)}:op.create_index(name,table,fields)
    if bind.dialect.name=='postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    result=bind.execute(sa.text('SELECT phone_number AS phone FROM whatsapp_conversation_messages UNION SELECT sender_phone FROM processed_whatsapp_messages UNION SELECT phone_number FROM whatsapp_conversations UNION SELECT mobile FROM leads').execution_options(stream_results=True))
    while True:
        rows=result.fetchmany(200)
        if not rows:break
        values=[phone_identity(row[0]) for row in rows if isinstance(row[0],str) and 0<len(row[0])<=64]
        if values:bind.execute(insert(phones).values(values).on_conflict_do_nothing(index_elements=['raw_phone']))
    result.close()
    result=bind.execute(sa.text("SELECT whatsapp_message_id,send_status,created_at FROM (SELECT whatsapp_message_id,send_status,created_at,ROW_NUMBER() OVER(PARTITION BY whatsapp_message_id ORDER BY id DESC) AS n FROM whatsapp_conversation_messages WHERE direction='outbound' AND whatsapp_message_id IS NOT NULL AND whatsapp_message_id<>'') ranked WHERE n=1").execution_options(stream_results=True))
    ranks={'sent':0,'failed':1,'delivered':2,'read':3}
    while True:
        rows=result.fetchmany(200)
        if not rows:break
        for provider,status,when in rows:
            status=(status or '').lower()
            if status not in ranks:continue
            if isinstance(when,str):when=datetime.fromisoformat(when)
            bind.execute(insert(delivery).values(provider_id=provider,status=status,status_at=when,rank=ranks[status]).on_conflict_do_nothing(index_elements=['provider_id']))
    result.close()


def downgrade():
    # Reporting metadata and actor snapshots are retained; the previous app ignores them.
    pass
