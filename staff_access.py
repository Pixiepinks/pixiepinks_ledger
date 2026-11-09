"""Shared section policy: grants are database-backed, never trusted from cookies."""
from fastapi import HTTPException

SECTIONS = (
    ('ad_analytics','Ad Analytics','/crm/ad-analytics'),
    ('marketing','Consent Management','/crm/marketing-consent'),
    ('crm','CRM Dashboard','/crm'),('finance','Finance Overview','/dashboard'),
    ('whatsapp','WhatsApp Inbox','/crm/whatsapp'),('quick_messages','Quick Messages','/crm/quick-messages'),
    ('saved_voices','Saved Voices','/crm/saved-voices'),('leads','CRM / Leads','/crm/leads'),
    ('bot','AI Bot Management','/crm/bot-management'),('customers','Customers','/customers'),
    ('intents','Orders / Sales Intents','/crm/orders'),('sales_orders','Sales Orders','/crm/sales-orders'),
    ('fulfillments','Fulfillments','/crm/fulfillments'),('items','Items','/items'),
    ('suppliers','Suppliers','/suppliers'),('journal','Journal','/entries'),
    ('accounts','Chart of Accounts','/accounts'),('trial_balance','Trial Balance','/reports/trial-balance'),
    ('income_statement','Income Statement','/reports/income-statement'),('balance_sheet','Balance Sheet','/reports/balance-sheet'),
)
KEYS={key for key,_,_ in SECTIONS}


def allowed(user, section):
    if user is None or getattr(user,'is_active',True) is False:return False
    if user.role=='ADMIN':return True
    if section=='ad_analytics':return isinstance(getattr(user,'section_access',None),list) and section in user.section_access
    grants=getattr(user,'section_access',None)
    if grants is None and section=='quick_messages':return user.role in {'ADMIN','SALES_MANAGER'}
    return grants is None or isinstance(grants,list) and section in grants


def managed_grant(user,section):
    return getattr(user,'section_access',None) is not None and allowed(user,section)


def section_for(path):
    if path.startswith("/crm/dashboard/"):return "crm"
    if path.startswith('/api/fulfillments/') or (path.startswith('/api/sales-orders/') and '/fulfillments' in path):return 'fulfillments'
    if path=='/api/sales-orders' or path.startswith('/api/sales-orders/'):return 'sales_orders'
    # Longest path wins; /crm does not implicitly grant its child sections.
    for key,_,prefix in sorted(SECTIONS,key=lambda row:len(row[2]),reverse=True):
        if path==prefix or path.startswith(prefix+'/'):
            if key=='crm' and path!='/crm':return None
            return key
    return None


def enforce(user,path):
    if getattr(user,'section_access',None) is None or user.role=='ADMIN':return
    if path in {'/','/staff-home','/logout'}:return
    section=section_for(path)
    if section is None or not allowed(user,section):raise HTTPException(403,'Your administrator has not granted access to this section')
    extra=('quick_messages' if path=='/crm/whatsapp/api/quick-messages' else
           'saved_voices' if '/send-saved-voice' in path or '/saved-voice-sends/' in path else None)
    if extra and not allowed(user,extra):raise HTTPException(403,'Your administrator has not granted access to '+extra.replace('_',' '))
    user._access_section=section  # Request-local ORM object; never a persisted role change.


def landing(user):
    if user.role=='ADMIN' or getattr(user,'section_access',None) is None:return '/dashboard'
    from settings import settings
    for key,_,path in SECTIONS:
        if key=='sales_orders' and not settings.SALES_ORDERS_ENABLED:continue
        if key=='fulfillments' and not settings.SALES_FULFILLMENT_ENABLED:continue
        if allowed(user,key):return path
    return '/staff-home'


def login_destination(user,next_url):
    if user.role=='ADMIN' or getattr(user,'section_access',None) is None:return next_url
    section=section_for(next_url.split('?',1)[0])
    for key,_,path in SECTIONS:
        if key==section and allowed(user,key):return path
    return landing(user)


def manager_grant(user):
    section=getattr(user,'_access_section',None)
    return section in {'quick_messages','saved_voices','bot'} and managed_grant(user,section)


def sales_permissions(user,legacy):
    if user.role=='ADMIN' or getattr(user,'section_access',None) is None:return set(legacy.get(user.role or 'STAFF',set()))
    permissions=set()
    if managed_grant(user,'sales_orders'):
        permissions|=(set(legacy.get(user.role or 'STAFF',set()))-{'fulfill_operate','fulfill_admin'})|{'create','edit','transition','record_payment_claim','dispatch'}
    if managed_grant(user,'fulfillments'):permissions.add('fulfill_operate')
    return permissions
