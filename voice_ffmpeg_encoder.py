"""Staff Variant C encoder, enabled by the production FFmpeg rollout flag."""
from collections import Counter
from threading import Lock, BoundedSemaphore
from time import monotonic

from audio_service import VoiceAudioError, _check_size, _signature
import voice_diagnostic_tools as tools
import voice_trace

COUNTERS=Counter()
LOCK=Lock()
SLOTS=BoundedSemaphore(2)
PARAMETERS={'codec':'libopus','container':'ogg','sample_rate':48000,'channels':1,'bit_rate':32000,
    'application':'voip','frame_duration_ms':20,'vbr':'on','threads':1,'mime_type':'audio/ogg',
    'duration_guard_seconds':601,'source_limit_seconds':600,'input_transport':'pipe',
    'input_protocols':['pipe'],'profile':'variant_c'}


def metrics():
    with LOCK:return {'scope':'current_process','process_instance':voice_trace.INSTANCE,**dict(COUNTERS)}


def normalize(raw,content_type=''):
    started=monotonic();outcome='error';reason='invalid_input';evidence={}
    acquired=False
    try:
        _check_size(raw)
        if not _signature(raw):raise VoiceAudioError('Unsupported or corrupted audio recording')
        acquired=SLOTS.acquire(blocking=False)
        if not acquired:
            reason='busy';raise VoiceAudioError('Audio encoder is busy; try again shortly')
        reason='tool_unavailable';runtime=tools.runtime()
        if not runtime.get('verified'):
            raise VoiceAudioError('Pinned FFmpeg encoder is unavailable or failed verification. No fallback was used; contact an administrator.')
        reason='invalid_source';source=tools.production_source(raw)
        reason='encode_failed';converted=tools.production_encode(raw)
        reason='invalid_output';info=tools.production_inspect(converted)
        reason='decode_failed';decoded=tools.production_decode(converted)
        duration=info['validation']['duration_seconds']
        if not decoded.get('passed') or abs(decoded['decoded_duration_seconds']-duration)>.05:
            raise ValueError('Independent output decode mismatch')
        if abs(duration-source['decoded_duration_seconds'])>.05:
            raise ValueError('Source/output duration mismatch')
        if info['warnings']:raise ValueError('Output structural warnings')
        evidence={'backend':'pinned_ffmpeg','source':voice_trace.fingerprint(raw),'output':voice_trace.fingerprint(converted),
            'source_format':source,'output_format':info['validation'],'parameters':PARAMETERS,
            'runtime':runtime,'independent_decode':decoded,'normalization_version':1}
        voice_trace.record('ffmpeg_normalization',evidence)
        outcome='success';reason='ok'
        return converted,round(duration,2)
    except VoiceAudioError:raise
    except (ValueError,OSError,KeyError,TypeError):
        raise VoiceAudioError('FFmpeg audio conversion or validation failed. No PyAV fallback was used; contact an administrator.') from None
    finally:
        if acquired:SLOTS.release()
        elapsed=round((monotonic()-started)*1000,3)
        with LOCK:
            COUNTERS['calls']+=1;COUNTERS[outcome]+=1;COUNTERS['elapsed_ms_total']+=elapsed
        voice_trace.record('audio_encoder_metric',{'backend':'pinned_ffmpeg','outcome':outcome,'reason':reason,'elapsed_ms':elapsed})
