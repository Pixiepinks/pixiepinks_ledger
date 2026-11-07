"""Phase 1 consent register. Schema is frozen; rollback retains evidence."""
from datetime import datetime
import re
import phonenumbers
from alembic import op
import sqlalchemy as sa
revision='20261106_25'
down_revision='20261103_24'
branch_labels=None
depends_on=None


def tables():
    metadata=sa.MetaData()
    sa.Table('users',metadata,sa.Column('id',sa.Integer(),primary_key=True))
    sa.Table('customers',metadata,sa.Column('id',sa.Integer(),primary_key=True))
    sa.Table('leads',metadata,sa.Column('id',sa.Integer(),primary_key=True))
    sa.Table('whatsapp_conversations',metadata,sa.Column('id',sa.Integer(),primary_key=True))
    t0=sa.Table('marketing_consents',metadata,
        sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
        sa.Column('identity_key',sa.String(100),nullable=False,unique=True),
        sa.Column('phone_e164',sa.String(18),unique=True),
        sa.Column('customer_id',sa.Integer(),sa.ForeignKey('customers.id',ondelete='SET NULL'),index=True),
        sa.Column('conversation_id',sa.Integer(),sa.ForeignKey('whatsapp_conversations.id',ondelete='SET NULL'),index=True),
        sa.Column('lead_id',sa.Integer(),sa.ForeignKey('leads.id',ondelete='SET NULL'),index=True),
        sa.Column('display_name',sa.String(255),nullable=False),
        sa.Column('identity_conflict',sa.Boolean(),nullable=False,server_default='0'),
        sa.Column('status',sa.String(24),nullable=False,index=True,server_default='NOT_REQUESTED'),
        sa.Column('scope',sa.String(100),nullable=False,server_default='PIXIEPINKS_WHATSAPP_PROMOTIONS'),
        sa.Column('source',sa.String(40),index=True),
        sa.Column('source_description',sa.Text()),
        sa.Column('consent_at',sa.DateTime()),
        sa.Column('wording_version',sa.String(100)),
        sa.Column('wording',sa.Text()),
        sa.Column('evidence_type',sa.String(30)),
        sa.Column('evidence_reference',sa.String(500)),
        sa.Column('evidence_at',sa.DateTime()),
        sa.Column('affirmative',sa.Boolean(),nullable=False,server_default='0'),
        sa.Column('verified_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('last_verified_at',sa.DateTime()),
        sa.Column('opted_out_at',sa.DateTime()),
        sa.Column('opt_out_source',sa.String(40)),
        sa.Column('opt_out_reason',sa.String(500)),
        sa.Column('interests',sa.JSON(),nullable=False),
        sa.Column('needs_review',sa.Boolean(),nullable=False,server_default='0'),
        sa.Column('active_request_id',sa.Integer()),
        sa.Column('version',sa.Integer(),nullable=False,server_default='0'),
        sa.Column('created_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('updated_by',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('created_at',sa.DateTime(),nullable=False,index=True),
        sa.Column('updated_at',sa.DateTime(),nullable=False,index=True),
        sa.CheckConstraint("status != 'OPTED_IN' OR (phone_e164 IS NOT NULL AND affirmative = true AND scope = 'PIXIEPINKS_WHATSAPP_PROMOTIONS' AND evidence_reference IS NOT NULL AND evidence_at IS NOT NULL AND wording IS NOT NULL AND wording_version IS NOT NULL AND consent_at IS NOT NULL AND last_verified_at IS NOT NULL)",name='ck_marketing_consent_evidence'),
        sa.CheckConstraint("status IN ('NOT_REQUESTED','PENDING','OPTED_IN','OPTED_OUT')",name='ck_marketing_consent_status'),
    )
    t1=sa.Table('marketing_consent_requests',metadata,
        sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
        sa.Column('consent_id',sa.Integer(),sa.ForeignKey('marketing_consents.id'),nullable=False,index=True),
        sa.Column('conversation_id',sa.Integer(),sa.ForeignKey('whatsapp_conversations.id',ondelete='SET NULL'),index=True),
        sa.Column('phone_e164',sa.String(18),nullable=False),
        sa.Column('actor_id',sa.Integer(),sa.ForeignKey('users.id',ondelete='SET NULL')),
        sa.Column('idempotency_key',sa.String(100),nullable=False,unique=True),
        sa.Column('language',sa.String(8),nullable=False),
        sa.Column('wording_version',sa.String(100),nullable=False),
        sa.Column('message_text',sa.Text(),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),
        sa.Column('base_version',sa.Integer(),nullable=False),
        sa.Column('whatsapp_message_id',sa.String(255),index=True),
        sa.Column('local_message_id',sa.Integer()),
        sa.Column('delivery_status',sa.String(24)),
        sa.Column('created_at',sa.DateTime(),nullable=False),
        sa.Column('sent_at',sa.DateTime()),
        sa.Column('expires_at',sa.DateTime(),nullable=False),
    )
    t2=sa.Table('marketing_consent_events',metadata,
        sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
        sa.Column('consent_id',sa.Integer(),sa.ForeignKey('marketing_consents.id'),nullable=False,index=True),
        sa.Column('dedup_key',sa.String(150),nullable=False,unique=True),
        sa.Column('action_fingerprint',sa.String(64)),
        sa.Column('event_type',sa.String(40),nullable=False),
        sa.Column('previous_status',sa.String(24),nullable=False),
        sa.Column('new_status',sa.String(24),nullable=False),
        sa.Column('customer_id',sa.Integer()),
        sa.Column('conversation_id',sa.Integer()),
        sa.Column('phone_e164',sa.String(18)),
        sa.Column('source',sa.String(40)),
        sa.Column('scope',sa.String(100),nullable=False),
        sa.Column('wording_version',sa.String(100)),
        sa.Column('wording',sa.Text()),
        sa.Column('evidence_type',sa.String(30)),
        sa.Column('evidence_reference',sa.String(500)),
        sa.Column('evidence_text',sa.Text()),
        sa.Column('evidence_at',sa.DateTime()),
        sa.Column('source_description',sa.Text()),
        sa.Column('actor_id',sa.Integer()),
        sa.Column('actor_name',sa.String(100),nullable=False),
        sa.Column('reason',sa.String(500)),
        sa.Column('recorded_at',sa.DateTime(),nullable=False,index=True),
        sa.Column('occurred_at',sa.DateTime(),nullable=False),
    )
    return t0,t1,t2


def normalized_phone(value):
    if not isinstance(value,str) or not re.fullmatch(r'[+0-9 ().-]{3,64}',value.strip()):return None
    digits=re.sub(r'\D','',value)
    if digits.startswith('00'):digits=digits[2:]
    if digits.startswith('0') and len(digits)==10:digits='94'+digits[1:]
    try:
        parsed=phonenumbers.parse('+'+digits,None)
        return phonenumbers.format_number(parsed,phonenumbers.PhoneNumberFormat.E164) if phonenumbers.is_possible_number(parsed) else None
    except phonenumbers.NumberParseException:return None


def upgrade():
    bind=op.get_bind();contacts,requests,events=tables()
    for table in (contacts,requests,events):
        table.create(bind,checkfirst=True)
        for index in table.indexes:index.create(bind,checkfirst=True)
    if bind.dialect.name=='postgresql':
        op.execute("""CREATE OR REPLACE FUNCTION marketing_events_immutable() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'Marketing consent events are append-only'; END; $$ LANGUAGE plpgsql""")
        op.execute('DROP TRIGGER IF EXISTS marketing_events_immutable ON marketing_consent_events')
        op.execute('CREATE TRIGGER marketing_events_immutable BEFORE UPDATE OR DELETE ON marketing_consent_events FOR EACH ROW EXECUTE FUNCTION marketing_events_immutable()')
    else:
        for action in ('UPDATE','DELETE'):
            op.execute(f"CREATE TRIGGER IF NOT EXISTS marketing_events_no_{action.lower()} BEFORE {action} ON marketing_consent_events BEGIN SELECT RAISE(ABORT, 'Marketing consent events are append-only'); END")
    backfill(bind,contacts,events)


def backfill(bind,contacts,events):
    scope='PIXIEPINKS_WHATSAPP_PROMOTIONS'
    def invalidate(previous,reason,conflict=False):
        now=datetime.utcnow()
        status='OPTED_OUT' if previous['status']=='OPTED_OUT' else 'NOT_REQUESTED'
        values={'status':status,'affirmative':False,'active_request_id':None,'version':previous['version']+1,'updated_at':now}
        if conflict:values['identity_conflict']=True
        changed=bind.execute(contacts.update().where(contacts.c.id==previous['id'],contacts.c.version==previous['version']).values(**values))
        if changed.rowcount:
            bind.execute(events.insert().values(consent_id=previous['id'],dedup_key=f'migration-identity:{previous["id"]}:{previous["version"]}',event_type='CONSENT_INVALIDATED',previous_status=previous['status'],new_status=status,customer_id=previous['customer_id'],conversation_id=previous['conversation_id'],phone_e164=previous['phone_e164'],scope=scope,actor_name='system:migration',reason=reason,occurred_at=now,recorded_at=now))
    if bind.dialect.name=='postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    for table,field,attr,label in [('customers','customer_id','phone','name'),('whatsapp_conversations','conversation_id','phone_number','customer_name'),('leads','lead_id','mobile','customer_name')]:
        result=bind.execute(sa.text(f'SELECT id, {attr}, {label} FROM {table}').execution_options(stream_results=True))
        while True:
            batch=result.mappings().fetchmany(200)
            if not batch:break
            for source in batch:
                normalized=normalized_phone(source[attr]);kind={'customers':'customer','whatsapp_conversations':'conversation','leads':'lead'}[table]
                key='wa:'+normalized if normalized else f'{kind}:{source["id"]}'
                now=datetime.utcnow()
                bind.execute(insert(contacts).values(identity_key=key,phone_e164=normalized,display_name=(source[label] or 'CRM contact')[:255],**{field:source['id']},status='NOT_REQUESTED',scope=scope,interests=[],created_at=now,updated_at=now).on_conflict_do_nothing(index_elements=['identity_key']))
                current=bind.execute(sa.select(contacts).where(contacts.c.identity_key==key)).mappings().one()
                if current[field] is None:
                    if field!='lead_id' and current['status'] in {'OPTED_IN','PENDING'}:invalidate(current,'A new canonical link requires fresh verification.')
                    bind.execute(contacts.update().where(contacts.c.id==current['id'],contacts.c[field].is_(None)).values({field:source['id']}))
                elif current[field]!=source['id'] and field!='lead_id' and not current['identity_conflict']:
                    invalidate(current,'Multiple canonical identities share this phone.',True)
                old=bind.execute(sa.select(contacts).where(contacts.c[field]==source['id'],contacts.c.identity_key!=key)).mappings().all()
                for previous in old:
                    if previous['status'] in {'OPTED_IN','PENDING'}:invalidate(previous,'Canonical phone changed during rollback; permission was not transferred.')
                    bind.execute(contacts.update().where(contacts.c.id==previous['id']).values({field:None}))
        result.close()


def downgrade():
    # Intentionally retain all evidence, requests, indexes and immutable triggers.
    # Old application code ignores these additive tables.
    pass
