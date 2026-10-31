"""Read-only runtime/attempt evidence and optional bounded Meta media retrieval.

No POST, conversion of production recordings, customer sends or settings writes.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4
from urllib.parse import urlparse

import av
import httpx
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

import audio_service
import build_info
import global_ai_control
import voice_trace
from models import SavedVoice, SavedVoiceSend, WhatsAppManualSend, WhatsAppConversationMessage
from settings import settings

INSTANCE=voice_trace.INSTANCE
STARTED=datetime.now(timezone.utc).isoformat()


def runtime_report(db, *, context="cli"):
    import io,wave
    import voice_ffmpeg_encoder
    result={'runtime':build_info.runtime(),'process_instance':INSTANCE,'diagnostics_loaded_at':STARTED,'execution_context':context,
        'normalization_version':audio_service.NORMALIZATION_VERSION,'pyav_version':av.__version__,
        'ffmpeg_libraries':{k:list(v) for k,v in av.library_versions.items()},
        'graph_version':settings.META_GRAPH_API_VERSION if re.fullmatch(r'v\d+\.\d+',settings.META_GRAPH_API_VERSION) else 'unrecognized',
        'meta_token_configured':bool(settings.META_WHATSAPP_ACCESS_TOKEN),
        'business_phone_id_sha256':hashlib.sha256(settings.META_WHATSAPP_PHONE_NUMBER_ID.encode()).hexdigest() if settings.META_WHATSAPP_PHONE_NUMBER_ID else None,
        'ffmpeg_encoder_enabled':audio_service.ffmpeg_encoder_enabled(),'ffmpeg_encoder_metrics':voice_ffmpeg_encoder.metrics()}
    try:
        result['alembic_revisions']=list(db.execute(text('SELECT version_num FROM alembic_version')).scalars())
        result['global_ai_enabled']=global_ai_control.state(db)['enabled']
    except SQLAlchemyError:
        db.rollback();result['database_status']='unavailable';result['global_ai_enabled']=None
    try:
        sample=io.BytesIO()
        with wave.open(sample,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*8000)
        encoded,_=audio_service.normalize_voice_audio(sample.getvalue(),'audio/wav')
        result['audio_self_check']={'passed':True,**audio_service.validate_voice_audio(encoded)}
    except Exception:
        result['audio_self_check']={'passed':False,'error':'Synthetic codec check failed'}
    return result


def _safe_source(row):
    from saved_voice_service import _read_file
    try:
        data=_read_file(row.original_storage_name or row.storage_name)
        info=audio_service.source_audio_info(data)
        return {**voice_trace.fingerprint(data),'container':info['container'],'codec':info['codec']}
    except Exception:return {'available':False,'error':'Source unavailable or undecodable'}


def _same_field(stages, key):
    a=stages.get('multipart_file',{}).get(key);b=stages.get('send_payload',{}).get(key)
    return a==b if a is not None and b is not None else None


def comparisons(trace):
    if not isinstance(trace,dict):return {'status':'unobserved_before_instrumentation','first_observed_mismatch':None}
    stages=trace.get('stages',{});names=['normalized_record','storage_read','upload_validated','multipart_file'] if trace.get('route')=='saved_voice' else ['normalized_memory','upload_validated','multipart_file']
    observed=[(name,stages.get(name)) for name in names if isinstance(stages.get(name),dict)]
    checks=[];first=None
    for (left,a),(right,b) in zip(observed,observed[1:]):
        valid=all(type(x.get('size_bytes')) is int and isinstance(x.get('sha256'),str) for x in (a,b))
        matches=(a['sha256']==b['sha256'] and a['size_bytes']==b['size_bytes']) if valid else None
        checks.append({'from':left,'to':right,'matches':matches})
        if matches is False and first is None:first=f'{left} -> {right}'
    missing=[n for n in names if n not in stages]
    uploaded=stages.get('upload_result',{}).get('media_id')
    referenced=stages.get('send_payload',{}).get('audio',{}).get('id')
    same_id=uploaded==referenced if uploaded and referenced else None
    if same_id is False and first is None:first='upload_result.media_id -> send_payload.audio.id'
    return {'status':'observed_mismatch' if first else 'incomplete_evidence' if missing else 'complete_matching_byte_evidence',
        'media_id_reference_matches':same_id,
        'business_phone_id_matches':_same_field(stages,'phone_id_sha256'),
        'graph_version_matches':_same_field(stages,'graph_version'),
        'first_observed_mismatch':first,'comparisons':checks,'unobserved_stages':missing,
        'comparison_scope':'Server byte boundaries; Meta download and phone playback require separate checks',
        'phone_playback':'not_observable_from_server'}


def _trace_schema(db, model):
    try:return 'audio_trace' in {column['name'] for column in inspect(db.connection()).get_columns(model.__tablename__)}
    except SQLAlchemyError:
        db.rollback();return False


def attempt_report(db, route, attempt_id):
    model=SavedVoiceSend if route=='saved' else WhatsAppManualSend
    if not _trace_schema(db,model):return {'error':'Trace column unavailable; verify schema 20261030_20. The standalone owner probe supports schema 19.'}
    row=db.get(model,attempt_id)
    if row is None or (route=='browser' and row.message_text!='Voice message'):return {'error':'Voice attempt not found'}
    result={'route':route,'attempt_id':row.id,'status':row.status,'whatsapp_message_id':row.whatsapp_message_id,
        'trace':row.audio_trace,'analysis':comparisons(row.audio_trace)}
    if route=='saved':
        result.update(recording_id=row.voice_id,recording_revision_at_send=row.voice_revision,meta_media_id=row.media_id,
            delivery_status=row.delivery_status,delivery_error_code=row.delivery_error_code)
        voice=db.get(SavedVoice,row.voice_id)
        if voice:
            from saved_voice_service import _read_file
            result['current_recording_revision']=voice.revision
            result['current_source_bytes']=_safe_source(voice)
            try:
                raw=_read_file(voice.storage_name)
                result['current_stored_bytes']=voice_trace.fingerprint(raw)
                result['current_stored_matches_record_metadata']=result['current_stored_bytes']=={'sha256':voice.sha256,'size_bytes':voice.size_bytes}
                try:result['current_stored_audio']={'passed':True,**audio_service.validate_voice_audio(raw)}
                except audio_service.VoiceAudioError:result['current_stored_audio']={'passed':False}
            except Exception:result['current_stored_bytes']={'available':False,'error':'Stored recording unavailable'}
            result['current_bytes_are_historical_send_proof']=False
    else:
        trace=row.audio_trace or {}
        result['meta_media_id']=trace.get('returned_media_id')
        result['delivery']=trace.get('delivery')
        if not result['meta_media_id'] and row.whatsapp_message_id:
            message=db.query(WhatsAppConversationMessage).filter_by(whatsapp_message_id=row.whatsapp_message_id,response_kind='staff_audio').first()
            if message:result['meta_media_id']=message.media_id;result['delivery']={'status':message.send_status}
    return result


def lookup_saved_message(db, message_id, recording_id=None):
    if not _trace_schema(db,SavedVoiceSend):return {'error':'Trace column unavailable; use the schema-19 standalone owner probe and verify migration status.'}
    query=db.query(SavedVoiceSend).filter_by(message_id=message_id)
    if recording_id is not None:query=query.filter_by(voice_id=recording_id)
    found=query.limit(2).all()
    if len(found)==1:return attempt_report(db,'saved',found[0].id)
    message=db.get(WhatsAppConversationMessage,message_id)
    info={'local_message_id':message_id,'recording_id_requested':recording_id,
          'recorded_response_kind':message.response_kind if message else None,
          'recorded_whatsapp_message_id':message.whatsapp_message_id if message else None,
          'recorded_media_id':message.media_id if message else None}
    return {**info,'error':'No uniquely matching Saved Voices attempt',
            'candidate_attempt_ids':[row.id for row in found]}


def recent_attempts(db):
    result=[]
    for route,model in [('saved',SavedVoiceSend),('browser',WhatsAppManualSend)]:
        if not _trace_schema(db,model):
            result.append({'route':route,'error':'Trace column missing; inspect actual migration status'});continue
        query=db.query(model)
        if route=='browser':query=query.filter_by(message_text='Voice message')
        for row in query.order_by(model.id.desc()).limit(10):
            result.append({'route':route,'attempt_id':row.id,'recording_id':row.voice_id if route=='saved' else None,
                'created_at_utc':row.created_at.isoformat(),'status':row.status,'trace_available':bool(row.audio_trace)})
    return result


def _hash(value):
    if not isinstance(value,str):return None
    if re.fullmatch(r'[0-9a-fA-F]{64}',value):return value.lower()
    try:
        decoded=base64.b64decode(value,validate=True)
        return decoded.hex() if len(decoded)==32 else None
    except ValueError:return None


def _metadata_error(response):
    try:
        error=response.json().get('error',{})
        return {key:value for key,value in {'error_code':error.get('code'),'error_subcode':error.get('error_subcode')}.items() if type(value) is int} if isinstance(error,dict) else {}
    except (ValueError,AttributeError):return {}


def retrieve_meta(media_id, expected=None, *, transport=None, offline_inspector=None):
    """Explicit operator-only GET/download; bytes and signed URLs never returned."""
    if not isinstance(media_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,255}',media_id):return {'error':'Invalid media ID'}
    version=settings.META_GRAPH_API_VERSION.strip().lstrip('/')
    if not re.fullmatch(r'v\d+\.\d+',version) or not settings.META_WHATSAPP_ACCESS_TOKEN:return {'error':'Meta configuration unavailable'}
    headers={'Authorization':'Bearer '+settings.META_WHATSAPP_ACCESS_TOKEN}
    try:
        with httpx.Client(timeout=20,follow_redirects=False,transport=transport) as client:
            response=client.get(f'https://graph.facebook.com/{version}/{media_id}',headers=headers)
            if response.status_code!=200:return {'error':'Meta metadata unavailable','http_status':response.status_code,**_metadata_error(response)}
            metadata=response.json();url=metadata.get('url');parsed=urlparse(url) if isinstance(url,str) else None
            host=(parsed.hostname or '').lower() if parsed else ''
            trusted=host in {'lookaside.fbsbx.com','graph.facebook.com'} or host.endswith('.fbcdn.net')
            if not parsed or parsed.scheme!='https' or not trusted or parsed.username or parsed.password or parsed.port not in (None,443):return {'error':'Meta returned an untrusted download URL'}
            size=metadata.get('file_size');size=size if type(size) is int and size>=0 else None
            if size is not None and size>audio_service.MAX_AUDIO_BYTES:return {'error':'Meta media exceeds size limit'}
            raw=bytearray()
            with client.stream('GET',url,headers=headers) as media:
                if media.status_code!=200:return {'error':'Meta download unavailable','http_status':media.status_code}
                for chunk in media.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>audio_service.MAX_AUDIO_BYTES:return {'error':'Meta media exceeds size limit'}
            actual=voice_trace.fingerprint(raw);declared=_hash(metadata.get('sha256'))
            mime=metadata.get('mime_type');mime=mime if isinstance(mime,str) and (re.fullmatch(r'audio/[A-Za-z0-9.+-]{1,40}',mime) or re.fullmatch(r'audio/ogg;\s*codecs=\"?opus\"?',mime,re.IGNORECASE)) else 'unrecognized'
            result={'downloaded':actual,'metadata':{'sha256':declared,'size_bytes':size,'mime_type':mime},
                'metadata_hash_matches':declared==actual['sha256'] if declared else None,
                'metadata_size_matches':size==len(raw) if size is not None else None,
                'expected_upload_matches':same_fingerprints(expected,actual)}
            try:
                info=audio_service.source_audio_info(bytes(raw))
                result['downloaded_format']={k:info.get(k) for k in ('container','codec','sample_rate','channels','duration_seconds','size_bytes')}
            except audio_service.VoiceAudioError:result['downloaded_format']={'recognized':False}
            try:result['ogg_opus_validation']={'passed':True,**audio_service.validate_voice_audio(bytes(raw))}
            except audio_service.VoiceAudioError as exc:result['ogg_opus_validation']={'passed':False,'reason':str(exc)}
            if offline_inspector is not None:
                result['offline_inspection']=offline_inspector(bytes(raw))
            return result
    except (httpx.HTTPError,ValueError,AttributeError):return {'error':'Meta retrieval failed; no credentials or response content included'}


def same_fingerprints(left, right):
    def valid(value):
        return (isinstance(value,dict) and isinstance(value.get('sha256'),str)
            and re.fullmatch(r'[a-f0-9]{64}',value['sha256']) is not None
            and type(value.get('size_bytes')) is int and value['size_bytes']>=0)
    return all(left[key]==right[key] for key in ('sha256','size_bytes')) if valid(left) and valid(right) else None


def expected_upload(report):
    trace=report.get('trace') or {};stages=trace.get('stages',{})
    value=stages.get('multipart_file') or stages.get('normalized_record') or stages.get('normalized_memory')
    return {k:value[k] for k in ('sha256','size_bytes')} if same_fingerprints(value,value) is True else None


if __name__=='__main__':
    import argparse
    from database import SessionLocal
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['runtime','trace'])
    parser.add_argument('--route',choices=['saved','browser'],default='saved')
    parser.add_argument('--attempt-id',type=int)
    parser.add_argument('--message-id',type=int,help='Local CRM message ID, not a send-attempt ID')
    parser.add_argument('--recording-id',type=int)
    parser.add_argument('--retrieve-meta',action='store_true',help='Read-only GET of the selected owner test media; sends nothing')
    args=parser.parse_args()
    if args.command=='trace' and (bool(args.attempt_id) == bool(args.message_id) or any(value is not None and value<1 for value in (args.attempt_id,args.message_id,args.recording_id))):parser.error('trace requires one positive --attempt-id or --message-id; optional --recording-id must be positive')
    if args.message_id and args.route!='saved':parser.error('--message-id lookup currently applies to Saved Voices')
    try:
        with SessionLocal() as db:
            report=runtime_report(db) if args.command=='runtime' else lookup_saved_message(db,args.message_id,args.recording_id) if args.message_id else attempt_report(db,args.route,args.attempt_id)
            if args.command=='trace' and args.retrieve_meta:
                report['meta_retrieval']=retrieve_meta(report.get('meta_media_id'),expected_upload(report))
                current=report.get('current_stored_bytes');download=report['meta_retrieval'].get('downloaded')
                report['current_saved_file_matches_downloaded_meta_media']=same_fingerprints(current,download)
            print(json.dumps(report,indent=2,default=str))
    except SQLAlchemyError:
        print(json.dumps({'error':'Database/schema unavailable. Check migration status; this command does not migrate or write.'}))
        raise SystemExit(1)
