"""Read-only owner-case probe, compatible with the existing 22b05f8/schema-19 app.

Can be copied to /tmp and run with PYTHONPATH=. without replacing web processes.
No sends, migrations, audio writes, conversions, token/phone/URL output.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re

from sqlalchemy import text
from database import SessionLocal
from settings import settings
from audio_service import NORMALIZATION_VERSION, validate_voice_audio, source_audio_info, VoiceAudioError
from saved_voice_service import _read_file
from whatsapp_service import download_whatsapp_media


def fp(raw):return {'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw)}


def check(raw):
    try:info=source_audio_info(raw)
    except VoiceAudioError:info={'recognized':False}
    try:return {'passed':True,**validate_voice_audio(raw),'actual_format':info}
    except VoiceAudioError as exc:return {'passed':False,'reason':str(exc),'actual_format':info}


def probe(db, message_id, recording_id, media_id, *, retrieve=False):
    # Select only columns present in schema 19, never the new trace column.
    row=db.execute(text('SELECT id,voice_id,voice_revision,status,media_id,whatsapp_message_id,message_id,delivery_status,delivery_error_code '
        'FROM saved_voice_sends WHERE message_id=:message AND voice_id=:voice'),{'message':message_id,'voice':recording_id}).mappings().all()
    if len(row)!=1:return {'error':'No unique matching Saved Voices attempt; no Meta retrieval performed'}
    attempt=dict(row[0])
    if attempt['media_id']!=media_id:return {'error':'Provided media ID does not match the stored attempt; no Meta retrieval performed'}
    voice=db.execute(text('SELECT revision,storage_name,sha256,size_bytes,normalization_version,processing_status '
        'FROM saved_voices WHERE id=:id'),{'id':recording_id}).mappings().one()
    platform=os.environ.get('RAILWAY_GIT_COMMIT_SHA','')
    report={'platform_reported_commit':platform if re.fullmatch(r'[a-fA-F0-9]{40}',platform) else None,
        'production_verified_by_this_workspace':False,
        'alembic_revisions':list(db.execute(text('SELECT version_num FROM alembic_version')).scalars()),
        'normalization_version':NORMALIZATION_VERSION,
        'global_ai_enabled':bool(db.execute(text('SELECT enabled FROM global_ai_reply_control WHERE id=1')).scalar()),
        'attempt':attempt,'current_recording_revision':voice['revision'],
        'revision_matches_attempt':voice['revision']==attempt['voice_revision'],
        'historical_upload_hash':'not_recorded_in_schema_19',
        'phone_playback':'not_observable_from_server'}
    try:
        raw=_read_file(voice['storage_name']);report['current_stored_bytes']=fp(raw)
        report['current_stored_matches_record_metadata']=fp(raw)=={'sha256':voice['sha256'],'size_bytes':voice['size_bytes']}
        report['current_stored_validation']=check(raw)
    except Exception:report['current_stored_bytes']={'error':'Stored bytes unavailable'}
    if retrieve:
        result=download_whatsapp_media(media_id)
        if result is None:report['meta_retrieval']={'error':'Media metadata/download unavailable; permission, expiry and processing state remain unverified'}
        else:
            raw,mime=result
            report['meta_retrieval']={'downloaded_bytes':fp(raw),'validation':check(raw),
                'mime_type':mime if isinstance(mime,str) and re.fullmatch(r'audio/[A-Za-z0-9.+-]{1,40}',mime) else 'unrecognized'}
            report['current_file_matches_meta_download']=report['current_stored_bytes']==fp(raw) if isinstance(report.get('current_stored_bytes'),dict) and 'sha256' in report['current_stored_bytes'] else None
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--message-id',type=int,required=True)
    parser.add_argument('--recording-id',type=int,required=True)
    parser.add_argument('--media-id',required=True)
    parser.add_argument('--retrieve-meta',action='store_true',help='GET the existing owner test media only; no sends')
    args=parser.parse_args()
    if args.message_id<1 or args.recording_id<1 or not re.fullmatch(r'\d{1,100}',args.media_id):parser.error('Use positive local IDs and a numeric Meta media ID')
    try:
        with SessionLocal() as db:result=probe(db,args.message_id,args.recording_id,args.media_id,retrieve=args.retrieve_meta)
        print(json.dumps(result,indent=2,default=str))
    except Exception:
        print(json.dumps({'error':'Read-only probe failed. Verify service/schema and access; no settings or messages changed.'}))
        raise SystemExit(1)
