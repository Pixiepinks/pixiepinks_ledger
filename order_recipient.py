"""Separate purchaser identity from order-specific Sri Lankan delivery contacts."""
import re
import phonenumbers
from marketing_identity import phone
FIELDS={'delivery_contact_person':255,'delivery_phone':30,'delivery_alternative_phone':30,'delivery_address':4000,'city':120,'district':120,'postal_code':30,'delivery_country':2,'delivery_notes':2000,'delivery_method':20}
def local_mobile(value):
    if not value:return None
    normalized=phone(value)
    if not normalized or not re.fullmatch(r'\+947\d{8}',normalized):raise ValueError('Please enter a valid Sri Lankan mobile number for the local delivery recipient.')
    return normalized

def customer_country(value):
    normalized=phone(value)
    return phonenumbers.region_code_for_number(phonenumbers.parse(normalized,None)) if normalized else None

def values(payload):
    result={}
    for key,limit in FIELDS.items():
        if key not in payload:continue
        value=payload[key]
        if value is None:value=''
        if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 and c not in '\n\r' for c in value):raise ValueError('Invalid '+key.replace('_',' '))
        result[key]=value.strip() or None
    if 'delivery_phone' not in payload and payload.get('delivery_contact_person') and str(payload.get('delivery_contact_person')).strip().casefold()==str(payload.get('customer_name','')).strip().casefold():
        try:result['delivery_phone']=local_mobile(payload.get('whatsapp_phone'))
        except ValueError:pass
    for key in ('delivery_phone','delivery_alternative_phone'):
        if result.get(key):
            try:result[key]=local_mobile(result[key])
            except ValueError:raise ValueError('Please enter a valid Sri Lankan mobile number for '+('the alternative delivery contact.' if key.endswith('alternative_phone') else 'the local delivery recipient.')) from None
    if result.get('delivery_country') not in (None,'LK'):raise ValueError('Local delivery country must be Sri Lanka (LK).')
    if result.get('delivery_method') not in (None,'DELIVERY','PICKUP','IN_STORE'):raise ValueError('Choose DELIVERY, PICKUP or IN_STORE.')
    return result

def shipping(order):
    if (order.delivery_method or 'DELIVERY')!='DELIVERY':return None
    required={'local recipient name':order.delivery_contact_person,'local delivery contact':order.delivery_phone,'delivery address':order.delivery_address,'city':order.city,'district':order.district}
    missing=[k for k,v in required.items() if not v or not str(v).strip()]
    if missing:raise ValueError('Local delivery contact required. Complete: '+', '.join(missing))
    return {'firstName':order.delivery_contact_person,'phone':local_mobile(order.delivery_phone),'address1':order.delivery_address,'city':order.city,'province':order.district,'zip':order.postal_code,'countryCode':'LK'}
