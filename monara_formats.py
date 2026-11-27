"""Bounded Google TTS audio normalization. No decoding guesses or raw header logging."""
import io,struct,wave
from email.message import Message
MIMES={'audio/l16','audio/wav','audio/x-wav','audio/wave','audio/pcm','audio/mpeg','audio/ogg','application/octet-stream'}
class ResponseFormatError(ValueError):
    def __init__(self,reason,audio=None):self.reason=reason;self.audio=audio or {}

def describe(value):
    """Return only known MIME enums and bounded numeric profile parameters."""
    out={}
    if not isinstance(value,str) or len(value)>256 or any(ord(c)<32 or ord(c)>126 for c in value):return {'mime_type':'OTHER'}
    m=Message();m['content-type']=value;base=m.get_content_type().lower()
    out['mime_type']=base if base in MIMES else 'OTHER'
    for k,v in (m.get_params() or [])[1:]:
        key={'rate':'sample_rate','channels':'channels','bits':'bit_depth','bit_depth':'bit_depth'}.get(k.lower())
        if key and isinstance(v,str) and v.isdigit() and len(v)<=6:
            n=int(v)
            if key=='sample_rate' and 8000<=n<=192000 or key=='channels' and 1<=n<=8 or key=='bit_depth' and n in {8,16,24,32}:out[key]=n
    return out

def profile(value):
    info=describe(value)
    if not isinstance(value,str) or info['mime_type']=='OTHER':raise ResponseFormatError('UNSUPPORTED_PCM_FORMAT',info)
    m=Message();m['content-type']=value;params={}
    for k,v in (m.get_params() or [])[1:]:
        k=k.lower()
        if k in params or not isinstance(v,str):raise ResponseFormatError('UNSUPPORTED_PCM_FORMAT',info)
        params[k]=v.lower()
    if set(params)-{'codec','rate','channels','bits','bit_depth'}:raise ResponseFormatError('UNSUPPORTED_PCM_FORMAT',info)
    if info['mime_type']=='audio/l16':
        if params.get('rate')!='24000' or params.get('codec','pcm')!='pcm' or params.get('channels','1')!='1' or params.get('bits','16')!='16' or params.get('bit_depth','16')!='16':raise ResponseFormatError('UNSUPPORTED_PCM_FORMAT',info)
        return 'pcm',info
    if info['mime_type'] in {'audio/wav','audio/x-wav','audio/wave'}:
        if params.get('codec','pcm')!='pcm':raise ResponseFormatError('UNSUPPORTED_PCM_FORMAT',info)
        return 'wav',info
    raise ResponseFormatError('UNSUPPORTED_PCM_FORMAT',info)

def normalize(raw,kind,info,max_bytes):
    if kind=='pcm':
        if not raw or len(raw)%2 or len(raw)>max_bytes:raise ResponseFormatError('INVALID_PCM_LENGTH',info)
        return raw
    if kind!='wav':raise ResponseFormatError('UNSUPPORTED_PCM_FORMAT',info)
    observed=dict(info)
    try:
        if len(raw)<44 or raw[:4]!=b'RIFF' or raw[8:12]!=b'WAVE' or struct.unpack('<I',raw[4:8])[0]+8!=len(raw):raise ValueError()
        with wave.open(io.BytesIO(raw),'rb') as w:
            observed.update(sample_rate=w.getframerate(),channels=w.getnchannels(),bit_depth=w.getsampwidth()*8)
            if w.getcomptype()!='NONE' or (w.getframerate(),w.getnchannels(),w.getsampwidth())!=(24000,1,2):raise ResponseFormatError('UNSUPPORTED_WAV_PROFILE',observed)
            if any(k in info and info[k]!=observed[k] for k in ('sample_rate','channels','bit_depth')):raise ResponseFormatError('UNSUPPORTED_WAV_PROFILE',observed)
            frames=w.getnframes()
            if not 0<frames*2<=max_bytes:raise ResponseFormatError('OUTPUT_LIMIT',observed)
            pcm=w.readframes(frames+1)
            if len(pcm)!=frames*2:raise ValueError()
        return pcm
    except ResponseFormatError:raise
    except (wave.Error,EOFError,ValueError,struct.error):raise ResponseFormatError('INVALID_WAV_CONTAINER',observed) from None
