"""Strict allowlist shared with the isolated SDK process; no exception messages."""
STAGES={'unknown','sdk_import','sdk_configuration','request_validation','client_setup','provider_request','provider_stream','response_validation','worker_launch','worker_result','encoder_setup','ogg_encoding','mp3_encoding','ogg_validation','mp3_validation','provider_execution','output_validation','storage_commit'}
CLASSES={'WorkerFailure','ResponseFormatError','MonaraAudioError','APIError','ClientError','ServerError','HTTPError','HTTPStatusError','ReadTimeout','ConnectTimeout','ConnectError','RemoteProtocolError','TimeoutError','TimeoutExpired','JSONDecodeError','KeyError','ValueError','TypeError','MemoryError','ImportError','ModuleNotFoundError','PackageNotFoundError','PermissionError','FileNotFoundError','OSError','CalledProcessError','OtherError'}
REASONS={'UNCLASSIFIED','SDK_CUSTOM_VOICE_UNSUPPORTED','SDK_VERSION_MISMATCH','UNAPPROVED_PROFILE','INVALID_TRANSCRIPT','INVALID_LANGUAGE','INVALID_STYLE','AMBIGUOUS_AUDIO_CANDIDATES','PROVIDER_TIMEOUT','PROVIDER_INTERRUPTED','UNEXPECTED_PROVIDER_TEXT','UNSUPPORTED_PCM_FORMAT','MIXED_AUDIO_FORMATS','EMPTY_AUDIO_PART','OUTPUT_LIMIT','INVALID_PCM_LENGTH','INVALID_PCM','DURATION_LIMIT','ENCODER_UNAVAILABLE','DURATION_MISMATCH','INVALID_MP3','MP3_DURATION_MISMATCH','UNSUPPORTED_WAV_PROFILE','INVALID_WAV_CONTAINER','INVALID_WORKER_OUTPUT','WORKER_FAILED','AUDIO_VALIDATION_FAILED'}

def clean(value):
    v=value if isinstance(value,dict) else {}
    out={'stage':v.get('stage') if isinstance(v.get('stage'),str) and v['stage'] in STAGES else 'unknown','exception_class':v.get('exception_class') if isinstance(v.get('exception_class'),str) and v['exception_class'] in CLASSES else 'OtherError','reason':v.get('reason') if isinstance(v.get('reason'),str) and v['reason'] in REASONS else 'UNCLASSIFIED'}
    for key,low,high in [('http_status',100,599),('exit_code',-255,255)]:
        if type(v.get(key)) is int and low<=v[key]<=high:out[key]=v[key]
    audio=v.get('audio')
    if isinstance(audio,dict):
        safe={}
        allowed={'audio/l16','audio/wav','audio/x-wav','audio/wave','audio/pcm','audio/mpeg','audio/ogg','application/octet-stream','OTHER'}
        if isinstance(audio.get('mime_type'),str) and audio['mime_type'] in allowed:safe['mime_type']=audio['mime_type']
        for key in ('sample_rate','channels','bit_depth'):
            n=audio.get(key)
            if type(n) is int and (key=='sample_rate' and 8000<=n<=192000 or key=='channels' and 1<=n<=8 or key=='bit_depth' and n in {8,16,24,32}):safe[key]=n
        if safe:out['audio']=safe
    return out

def diagnostic(stage,exc=None,reason=None,exit_code=None,audio=None):
    value={'stage':stage,'exception_class':type(exc).__name__ if exc is not None else 'OtherError','reason':reason,'exit_code':exit_code,'audio':audio}
    for candidate in (getattr(exc,'code',None),getattr(exc,'status_code',None)):
        if type(candidate) is int and 100<=candidate<=599:value['http_status']=candidate;break
    return clean(value)
