"""Isolated google-genai runtime. stdin transcript, stdout bounded JSON; never logs inputs."""
import base64,json,os,re,sys,time
from monara_diagnostics import diagnostic
STAGE='sdk_import'
class WorkerFailure(ValueError):
    def __init__(self,reason):self.reason=reason

MODEL='gemini-3.8-flash-tts';VOICE='voice_xu8nahptj1p9';VERSION='v1beta'

def config(types):
    if 'voice' not in types.VoiceConfig.model_fields:raise WorkerFailure('SDK_CUSTOM_VOICE_UNSUPPORTED')
    return types.GenerateContentConfig(temperature=1,response_modalities=['audio'],speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(voice=VOICE)))

def collect(stream,*,max_bytes=4320000,deadline=60):
    global STAGE
    STAGE='provider_stream'
    parts=[];mime=None;size=0;started=time.monotonic();usage={}
    for chunk in stream:
        STAGE='response_validation'
        if len(chunk.candidates or [])>1:raise WorkerFailure('AMBIGUOUS_AUDIO_CANDIDATES')
        if time.monotonic()-started>deadline:raise WorkerFailure('PROVIDER_TIMEOUT')
        for candidate in chunk.candidates or []:
            if getattr(candidate,'finish_reason',None) and str(candidate.finish_reason).split('.')[-1] not in {'STOP','FINISH_REASON_UNSPECIFIED'}:raise WorkerFailure('PROVIDER_INTERRUPTED')
            content=getattr(candidate,'content',None)
            for part in getattr(content,'parts',None) or []:
                inline=getattr(part,'inline_data',None)
                if inline is None:
                    if getattr(part,'text',None):raise WorkerFailure('UNEXPECTED_PROVIDER_TEXT')
                    continue
                value=inline.mime_type or ''
                if not re.fullmatch(r'audio/L16;\s*codec=pcm;\s*rate=24000',value,re.I):raise WorkerFailure('UNSUPPORTED_PCM_FORMAT')
                if mime is not None and value.lower()!=mime.lower():raise WorkerFailure('MIXED_AUDIO_FORMATS')
                raw=inline.data
                if not isinstance(raw,bytes) or not raw:raise WorkerFailure('EMPTY_AUDIO_PART')
                mime=value;size+=len(raw)
                if size>max_bytes:raise WorkerFailure('OUTPUT_LIMIT')
                parts.append(raw)
        meta=getattr(chunk,'usage_metadata',None)
        if meta:
            for key in ('prompt_token_count','candidates_token_count','total_token_count'):
                value=getattr(meta,key,None)
                if type(value) is int and value>=0:usage[key]=value
        STAGE='provider_stream'
    STAGE='response_validation'
    raw=b''.join(parts)
    if not raw or len(raw)%2:raise WorkerFailure('INVALID_PCM_LENGTH')
    return {'pcm':base64.b64encode(raw).decode(),'mime':mime,'usage':usage}

def main():
    global STAGE
    STAGE='sdk_import'
    if sys.platform=='linux':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(35,35))
    from importlib.metadata import version
    if version('google-genai')!='3.6.1':raise WorkerFailure('SDK_VERSION_MISMATCH')
    from google import genai
    from google.genai import types
    STAGE='sdk_configuration'
    cfg=config(types)
    options=types.HttpOptions(api_version=VERSION,timeout=60000,retry_options=types.HttpRetryOptions(attempts=1))
    if sys.argv[1:]==['--capabilities']:
        print(json.dumps({'sdk':__import__('importlib.metadata',fromlist=['version']).version('google-genai'),'custom_voice_supported':True,'config':cfg.model_dump(mode='json',exclude_none=True),'model':MODEL,'api_version':VERSION,'style_supported':'speech_metadata' in types.Part.model_fields,'language_code_supported':'language_code' in types.SpeechConfig.model_fields,'http_options':options.model_dump(mode='json',exclude_none=True)}));return
    STAGE='request_validation'
    payload=json.loads(sys.stdin.buffer.read(32000))
    if payload.get('model')!=MODEL or payload.get('voice')!=VOICE or payload.get('api_version')!=VERSION:raise WorkerFailure('UNAPPROVED_PROFILE')
    transcript=payload['transcript']
    if not isinstance(transcript,str) or not 1<=len(transcript)<=1000:raise WorkerFailure('INVALID_TRANSCRIPT')
    language=payload.get('language','si')
    if language not in ('si','en','ta'):raise WorkerFailure('INVALID_LANGUAGE')
    cfg.speech_config.language_code=language
    style=payload.get('style_prompt','')
    if not isinstance(style,str) or len(style)>1000:raise WorkerFailure('INVALID_STYLE')
    part=types.Part.from_text(text='## Transcript:\n'+transcript)
    if style:part.speech_metadata=types.SpeechMetadata(style=style)
    # No SDK retry: one billable invocation per durable job claim.
    STAGE='client_setup'
    with genai.Client(api_key=os.environ['GEMINI_API_KEY'],http_options=types.HttpOptions(api_version=VERSION,timeout=int(payload['timeout'])*1000,retry_options=types.HttpRetryOptions(attempts=1))) as client:
        STAGE='provider_request'
        stream=client.models.generate_content_stream(model=MODEL,contents=[types.Content(role='user',parts=[part])],config=cfg)
        print(json.dumps(collect(stream,deadline=int(payload['timeout']),max_bytes=int(payload['max_pcm_bytes']))))
if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Deliberately suppress raw SDK errors, URLs, response bodies and transcript.
        print(json.dumps({'error':'PROVIDER_UNAVAILABLE_OR_INVALID_AUDIO','diagnostic':diagnostic(STAGE,exc,exc.reason if isinstance(exc,WorkerFailure) else None)}));raise SystemExit(1)
