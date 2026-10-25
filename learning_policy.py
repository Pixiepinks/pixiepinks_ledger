"""Conservative deterministic privacy/risk gates; model classifications never authorize."""
import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal

ANALYZER_VERSION='1'
EVALUATION_VERSION='1'
# Reviewed initial promotion surface. Novel wording always needs human review.
COMMUNICATION={
    'greeting.en':'Hello! How can I help you today?',
    'greeting.si':'ආයුබෝවන්! ඔබට අද මොනවද අවශ්‍ය?',
    'clarification.en':'Could you tell me a little more about what you need?',
    'clarification.si':'ඔබට අවශ්‍ය දේ ගැන තව ටිකක් කියන්න පුළුවන්ද?',
    'acknowledgement.en':'Thank you for sharing that.',
    'acknowledgement.mixed':'ස්තුතියි! Thank you for sharing that.',
}
DEFAULTS={'enabled':False,'privacy_reviewed':False,'notice_configured':False,'provider_reviewed':False,
    'publication_enabled':False,'auto_publish':False,'auto_paused':False,'schedule':'02:00',
    'timezone':'Asia/Colombo','min_evidence':3,'confidence':0.95,'max_conversations':10,
    'max_tokens':100000,'daily_tokens':250000,'max_cost':1.0,'daily_cost':2.0,
    'usd_per_million_tokens':10.0,'model':'gpt-4.1-mini','max_auto_per_day':1,
    'auto_allowlist':['greeting.en','greeting.si','clarification.en','clarification.si'],
    'evidence_days':30,'candidate_days':180,'evaluation_days':90,'job_log_days':30,
    'audit_days':365,'legal_hold':False,'notify_in_app':True,'quiet_hours':24,'catchup_days':7,'lookback_days':30}
INJECTION=re.compile(r'ignore.{0,30}(instruct|previous|system)|system\s*prompt|developer\s*message|disable.{0,25}(safe|security|guard)|reveal.{0,25}(key|secret|prompt)|override|jailbreak|api[_ -]?key|access[_ -]?token|password|මුරපද|උපදෙස්.{0,20}නොසලකා',re.I|re.S)
EMAIL=re.compile(r'\b[^\s@]+@[^\s@]+\.[^\s@]+\b')
PHONE=re.compile(r'(?<!\w)\+?\d[\d ()-]{6,}\d(?!\w)')
URL=re.compile(r'https?://\S+|www\.\S+',re.I)
SECRET=re.compile(r'\b(?:sk-[A-Za-z0-9_-]{8,}|EAA[A-Za-z0-9]{12,}|eyJ[A-Za-z0-9_.-]{20,}|\d{9}[vVxX]|\d{12})\b')
SENSITIVE=re.compile(r'address|bank|account\s*(number|no)|card\s*(number|no)|nic\b|passport|payment\s*reference|ලිපිනය|ගිණුම්|හැඳුනුම්|\b(street|mawatha|road|lane)\b',re.I)
CURRENCY=re.compile(r'\b(?:lkr|usd|rupees|rs\.?)\b|(?<!\w)රු(?:\.|\s|\d)|රුපියල්|\bcosts?\b.{0,30}\b(?:thousand|hundred)\b|\b(?:rs\.?|lkr|usd|price|discount|stock)\s*[:=]?\s*\d|\d[\d,.]*\s*(?:රු|rupees)|\bin stock\b|\bout of stock\b|රු\.?\s*\d',re.I)
POLICY=re.compile(r'deliver|refund|warrant|return|payment|discount|service\s*charge|day|days|free|assembly|policy|දින|බෙදා|ගාස්තු|වට්ටම්|ගෙවීම්',re.I)
PRODUCT=re.compile(r'height|age|size|wheel|bicycle|product|color|stock|price|eligible|සුදුසු|වයස|උස|බයිසිකල්|අඟල්',re.I)


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC',text).casefold().split())


def content_hash(text,language,handle=None):
    return hashlib.sha256((normalized(text)+'|'+language+'|'+(handle or '')).encode()).hexdigest()


def sanitize(text,known=()):
    if not isinstance(text,str):return ''
    text=unicodedata.normalize('NFC',text)[:4000]
    for value in sorted(set(value for value in known if isinstance(value,str) and len(value.strip())>=3),key=len,reverse=True):
        text=re.sub(re.escape(value),'[PRIVATE]',text,flags=re.I)
    text=re.sub(r'\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*','[PRIVATE]',text)
    text=re.sub(r'\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b','[PRIVATE]',text)
    text=SECRET.sub('[SECRET]',text)
    text=EMAIL.sub('[EMAIL]',text)
    text=URL.sub('[URL]',text)
    text=PHONE.sub('[PHONE]',text)
    text=re.sub(r'\b(?:SO-|gid://|order\s*(?:no|number|ref)?\s*[:#])\S+','[ORDER_REFERENCE]',text,flags=re.I)
    text=re.sub(r'(?im)^(?:my name is|name\s*:|i am\s+[A-Z]|customer\s*:|contact\s*:|address\s*:|මගේ නම|ලිපිනය).*$','[PRIVATE]',text)
    # Address/payment statements are excluded as a whole, not sent for AI redaction.
    if SENSITIVE.search(text):return '[PRIVATE]'
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]','',text).strip()[:800]


def prohibited(text):
    return bool(INJECTION.search(text) or SECRET.search(text) or EMAIL.search(text) or PHONE.search(text)
                or URL.search(text) or SENSITIVE.search(text) or re.search(r'\b(system|developer|prompt|instructions?|credentials?|secrets?|tokens?|database|execute|jailbreak)\b|උපදෙස්|රහස්',text,re.I) or '[' in text or '<' in text or '>' in text)


def classify(text,suggested='communication'):
    if prohibited(text):return 'sensitive','PROHIBITED'
    if text in COMMUNICATION.values():return 'communication','LOW'
    if POLICY.search(text):return 'policy','HIGH'
    if PRODUCT.search(text) or suggested in {'product','correction'}:return 'product','HIGH'
    return 'communication','REVIEW'  # Unknown language/wording is never automatically safe.


def similar(left,right):
    a,b=normalized(left),normalized(right)
    if not a or not b:return False
    words_a,words_b=set(a.split()),set(b.split())
    return SequenceMatcher(None,a,b).ratio()>=0.88 or len(words_a&words_b)/max(1,len(words_a|words_b))>=0.8


class Lesson(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    category:Literal['communication','product','policy','correction']
    title:str=Field(min_length=1,max_length=120)
    proposed_content:str=Field(min_length=1,max_length=1000)
    language:Literal['en','si','mixed']
    confidence:float=Field(ge=0,le=1)
    source_message_ids:list[int]=Field(min_length=1,max_length=12)
    product_handle:str|None=Field(default=None,max_length=255)
    product_category:str=Field(default='',max_length=100)
    reason:str=Field(default='',max_length=300)
    intent:str=Field(default='',max_length=120)
    response_strategy:str=Field(default='',max_length=200)
    potential_correction:bool=False


class Extraction(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    lessons:list[Lesson]=Field(max_length=5)


def validate_settings(values):
    if not isinstance(values,dict) or set(values)-set(DEFAULTS):raise ValueError('Unknown learning setting')
    result={**DEFAULTS,**values}
    for key,default in DEFAULTS.items():
        value=result[key]
        if type(default) is bool and type(value) is not bool:raise ValueError('Boolean setting required')
        if type(default) is int and (type(value) is not int or not 0<=value<=1000000):raise ValueError('Invalid numeric setting')
        if type(default) is float and (type(value) not in {int,float} or not 0<=value<=1000):raise ValueError('Invalid budget or threshold')
    for key in ['min_evidence','max_conversations','max_tokens','daily_tokens','max_cost','daily_cost','usd_per_million_tokens','evidence_days','candidate_days','evaluation_days','job_log_days','audit_days']:
        if result[key]<=0:raise ValueError('Limits and retention must be positive')
    if not 0.9<=result['confidence']<=1 or not 1<=result['max_conversations']<=100 or not 0<=result['max_auto_per_day']<=5 or not 0<=result['quiet_hours']<=168 or not 1<=result['catchup_days']<=7:
        raise ValueError('Unsafe learning limits')
    if not 1<=result['lookback_days']<=90:raise ValueError('Learning lookback must be 1–90 days')
    if not isinstance(result['model'],str) or not isinstance(result['timezone'],str) or result['model'] not in {'gpt-4.1-mini','gpt-4o-mini','gpt-4.1','gpt-4o'} or result['timezone']!='Asia/Colombo':raise ValueError('Unsupported model/timezone')
    if not isinstance(result['schedule'],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',result['schedule']):raise ValueError('Use HH:MM schedule')
    if not isinstance(result['auto_allowlist'],list) or not all(isinstance(x,str) and x in COMMUNICATION for x in result['auto_allowlist']):raise ValueError('Unreviewed automatic phrase')
    return result
