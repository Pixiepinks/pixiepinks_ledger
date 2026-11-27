"""Isolated google-genai runtime. stdin transcript, stdout bounded JSON; never logs inputs."""
import base64,json,os,re,sys,time
MODEL='gemini-3.8-flash-tts';VOICE='voice_xu8nahptj1p9';VERSION='v1beta'

def config(types):
    if 'voice' not in types.VoiceConfig.model_fields:raise ValueError('SDK_CUSTOM_VOICE_UNSUPPORTED')
    return types.GenerateContentConfig(temperature=1,response_modalities=['audio'],speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(voice=VOICE)))

def collect(stream,*,max_bytes=4320000,deadline=60):
    parts=[];mime=None;size=0;started=time.monotonic();usage={}
    for chunk in stream:
        if len(chunk.candidates or [])>1:raise ValueError('AMBIGUOUS_AUDIO_CANDIDATES')
        if time.monotonic()-started>deadline:raise ValueError('PROVIDER_TIMEOUT')
        for candidate in chunk.candidates or []:
            if getattr(candidate,'finish_reason',None) and str(candidate.finish_reason).split('.')[-1] not in {'STOP','FINISH_REASON_UNSPECIFIED'}:raise ValueError('PROVIDER_INTERRUPTED')
            content=getattr(candidate,'content',None)
            for part in getattr(content,'parts',None) or []:
                inline=getattr(part,'inline_data',None)
                if inline is None:
                    if getattr(part,'text',None):raise ValueError('UNEXPECTED_PROVIDER_TEXT')
                    continue
                value=inline.mime_type or ''
                if not re.fullmatch(r'audio/L16;\s*codec=pcm;\s*rate=24000',value,re.I):raise ValueError('UNSUPPORTED_PCM_FORMAT')
                if mime is not None and value.lower()!=mime.lower():raise ValueError('MIXED_AUDIO_FORMATS')
                raw=inline.data
                if not isinstance(raw,bytes) or not raw:raise ValueError('EMPTY_AUDIO_PART')
                mime=value;size+=len(raw)
                if size>max_bytes:raise ValueError('OUTPUT_LIMIT')
                parts.append(raw)
        meta=getattr(chunk,'usage_metadata',None)
        if meta:
            for key in ('prompt_token_count','candidates_token_count','total_token_count'):
                value=getattr(meta,key,None)
                if type(value) is int and value>=0:usage[key]=value
    raw=b''.join(parts)
    if not raw or len(raw)%2:raise ValueError('INVALID_PCM_LENGTH')
    return {'pcm':base64.b64encode(raw).decode(),'mime':mime,'usage':usage}

def main():
    if sys.platform=='linux':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(35,35))
    from google import genai
    from google.genai import types
    cfg=config(types)
    options=types.HttpOptions(api_version=VERSION,timeout=60000,retry_options=types.HttpRetryOptions(attempts=1))
    if sys.argv[1:]==['--capabilities']:
        print(json.dumps({'sdk':__import__('importlib.metadata',fromlist=['version']).version('google-genai'),'custom_voice_supported':True,'config':cfg.model_dump(mode='json',exclude_none=True),'model':MODEL,'api_version':VERSION,'style_supported':False,'http_options':options.model_dump(mode='json',exclude_none=True)}));return
    payload=json.loads(sys.stdin.buffer.read(16000))
    if payload.get('model')!=MODEL or payload.get('voice')!=VOICE or payload.get('api_version')!=VERSION:raise ValueError('UNAPPROVED_PROFILE')
    transcript=payload['transcript']
    if not isinstance(transcript,str) or not 1<=len(transcript)<=1000:raise ValueError('INVALID_TRANSCRIPT')
    # No SDK retry: one billable invocation per durable job claim.
    with genai.Client(api_key=os.environ['GEMINI_API_KEY'],http_options=types.HttpOptions(api_version=VERSION,timeout=int(payload['timeout'])*1000,retry_options=types.HttpRetryOptions(attempts=1))) as client:
        stream=client.models.generate_content_stream(model=MODEL,contents=[types.Content(role='user',parts=[types.Part.from_text(text='## Transcript:\n'+transcript)])],config=cfg)
        print(json.dumps(collect(stream,deadline=int(payload['timeout']),max_bytes=int(payload['max_pcm_bytes']))))
if __name__=='__main__':
    try:main()
    except Exception:
        # Deliberately suppress raw SDK errors, URLs, response bodies and transcript.
        print(json.dumps({'error':'PROVIDER_UNAVAILABLE_OR_INVALID_AUDIO'}));raise SystemExit(1)
