"""Bounded owner-only error evidence, never request bodies or recipient fields."""
import re
from urllib.parse import urlsplit


def clean(value):
    if not isinstance(value,str):return None
    value=value[:8192]
    value=re.sub(r'(?i)\bBearer\s+\S+', '[REDACTED]',value)
    value=re.sub(r'\bEA[A-Za-z0-9_-]{20,}\b','[REDACTED]',value)
    value=re.sub(r'(?i)(access_token|authorization|api_key|token|secret)\s*[:=]\s*[^\s,;]+',r'\1=[REDACTED]',value)
    value=re.sub(r'(?<!\w)\+?\d[\d ()-]{6,}\d(?!\w)','[REDACTED NUMBER]',value)
    value=re.sub(r'https?://[^\s<>]+','[REDACTED URL]',value)
    return value


def errors(values):
    result=[]
    for error in values[:16] if isinstance(values,list) else []:
        if not isinstance(error,dict):continue
        item={k:error[k] for k in ('code','error_subcode') if type(error.get(k)) is int}
        for key in ('title','message'):
            if isinstance(error.get(key),str):item[key]=clean(error[key])
        data=error.get('error_data')
        if isinstance(data,dict):
            item['error_data']={key:clean(data[key]) for key in ('details','messaging_product') if isinstance(data.get(key),str)}
        href=error.get('href')
        if isinstance(href,str):
            try:
                parsed=urlsplit(href)
                allowed=parsed.scheme=='https' and parsed.netloc=='developers.facebook.com' and parsed.path.startswith('/docs/') and not parsed.query and not parsed.fragment
            except ValueError:allowed=False
            item['href']=href[:2048] if allowed else '[REDACTED URL]'
        result.append(item)
    return result
