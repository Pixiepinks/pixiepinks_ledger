"""One-message verification of the actual public production candidate path."""
from copy import deepcopy
from datetime import datetime,timedelta
import hmac
import json
import os
import re
from uuid import uuid4

from fastapi import HTTPException
from models import SavedVoice,VoicePlaybackTest,VoicePlaybackVariant
from saved_voice_service import _read_file,_read_original,write_audio,storage_path
import audio_service
import voice_diagnostic_tools as tools
import voice_playback_test as owner
import voice_encoding_experiment as experiment
import voice_trace
import whatsapp_service

KIND='production_ffmpeg_candidate'


def configuration():
    if os.getenv('VOICE_FFMPEG_OWNER_TEST_ENABLED','').lower()!='true':raise HTTPException(409,'Candidate owner tests are disabled')
    return owner.configuration()


def get_run(db,user,run_id):
    run=owner.get_run(db,user,run_id)
    if run.evidence.get('kind')!=KIND:raise HTTPException(409,'Choose a candidate verification run')
    return run


def prepare(db,user,reference_id,voice_id,revision,typed_recipient,confirmed,mode):
    owner.authorize(user);recipient=configuration()
    if not confirmed or not re.fullmatch(r'[1-9][0-9]{7,14}',typed_recipient) or not hmac.compare_digest(recipient,typed_recipient):
        raise HTTPException(400,'Confirm owner/source authorization and the configured destination')
    if mode not in {'native','ordinary'}:raise HTTPException(422,'Choose native production delivery or ordinary-audio control')
    reference=experiment.get_run(db,user,reference_id)
    if reference.recipient_hash!=owner.binding(recipient):raise HTTPException(409,'Reference belongs to different owner/business configuration')
    recorded=(reference.playback or {}).get('variants',{}).get('C',{}).get('result')
    if recorded not in {None,'not_tested','plays'}:raise HTTPException(409,'Reference phone record does not confirm C plays; review it manually')
    control=db.query(VoicePlaybackVariant).filter_by(run_id=reference.id,variant='C').one_or_none()
    if not control or control.status!='SENT':raise HTTPException(409,'Reference C must have an accepted owner send')
    runtime=reference.evidence.get('independent_runtime',{})
    if not runtime.get('verified') or runtime.get('binary_sha256')!=tools.BINARY_SHA256:
        raise HTTPException(409,'Reference did not use the reviewed pinned executable')
    voice=db.get(SavedVoice,voice_id)
    if not voice or not voice.active or voice.processing_status!='READY' or voice.revision!=revision:
        raise HTTPException(409,'Select a current ready newly recorded sample')
    if voice.created_at<=reference.created_at or voice.id==reference.voice_id:
        raise HTTPException(409,'Upload a new owner recording after the completed reference experiment')
    normalized=_read_original(voice)
    raw=_read_file(voice.original_storage_name) if voice.original_storage_name else normalized
    tools.inspect_source(raw)  # Owner test stays short even though production supports 10 minutes.
    source=voice_trace.fingerprint(raw)
    if source==reference.evidence.get('source'):raise HTTPException(409,'Use a different/new owner sample, not the previous experiment source')
    expected=(voice.audio_diagnostics or {}).get('source',{}).get('sha256')
    if expected and voice.original_storage_name and expected!=source['sha256']:raise HTTPException(409,'Retained source integrity failed')
    owner.window(db,recipient)
    stored=[]
    try:
        trace=voice_trace.new('owner_production_ffmpeg_candidate')
        # Calls the identical public entry point used by Saved Voices and browser
        # recordings; the private ContextVar scope does not alter the global flag.
        global_enabled=os.getenv('VOICE_FFMPEG_ENCODER_ENABLED','').lower()=='true'
        with voice_trace.capture(trace),audio_service.owner_ffmpeg_candidate():
            encoded,duration=audio_service.normalize_voice_audio(raw,'application/octet-stream')
        info=tools.production_inspect(encoded)
        if trace.get('stages',{}).get('ffmpeg_normalization',{}).get('backend')!='pinned_ffmpeg':
            raise ValueError('Public candidate path was not observed')
        source_name=write_audio(raw,original=True);stored.append(source_name)
        output_name=write_audio(encoded);stored.append(output_name)
        evidence=voice_trace.new('owner_production_ffmpeg_candidate')
        evidence.update(kind=KIND,experiment_id=uuid4().hex,source=source,normalization_trace=trace,
            reference={'run_id':reference.id,'owner_attested_c_plays':True,'stored_playback':reference.playback,
                'stored_playback_missing':reference.playback is None},
            global_production_flag_at_prepare=global_enabled,delivery_mode=mode,
            message_type={'type':'audio','audio':{'id':'<new-media-id>',**({'voice':True} if mode=='native' else {})}},
            variants={'P':{'label':'Public production FFmpeg candidate','storage_name':output_name,
                'bytes':voice_trace.fingerprint(encoded),'audio':info,'duration_seconds':duration,'eligible':True}})
        run=VoicePlaybackTest(id=evidence['experiment_id'],staff_id=user.id,voice_id=voice.id,voice_revision=revision,
            storage_name=source_name,recipient_hash=owner.binding(recipient),status='REVIEW',
            evidence=experiment.audit(db,evidence,user,'CANDIDATE_PREPARED',message_count=1,reference_id=reference.id,mode=mode))
        db.add(run);db.flush()
        db.add(VoicePlaybackVariant(run_id=run.id,variant='P',status='NOT_STARTED',
            audio_trace={'idempotency_key':'candidate:'+run.id+':P'}))
        db.commit();return run
    except Exception:
        db.rollback()
        for name in stored:storage_path(name).unlink(missing_ok=True)
        raise


def execute(db,user,run_id,confirmed,count):
    run=get_run(db,user,run_id);recipient=configuration()
    if not confirmed or count!=1:raise HTTPException(400,'Explicitly authorize exactly one owner message')
    if run.recipient_hash!=owner.binding(recipient):raise HTTPException(409,'Owner/business configuration changed')
    if run.status!='REVIEW':return run
    if datetime.utcnow()-run.created_at>timedelta(minutes=15):raise HTTPException(409,'Candidate authorization expired')
    variant=db.query(VoicePlaybackVariant).filter_by(run_id=run.id,variant='P').one()
    if variant.status!='NOT_STARTED':raise HTTPException(409,'A candidate attempt already exists; no retry is permitted')
    owner.window(db,recipient)
    value=run.evidence['variants']['P'];raw=_read_file(value['storage_name'])
    if voice_trace.fingerprint(raw)!=value['bytes']:raise HTTPException(409,'Reviewed candidate changed')
    if voice_trace.fingerprint(_read_file(run.storage_name))!=run.evidence['source']:raise HTTPException(409,'Reviewed source changed')
    if not tools.runtime().get('verified'):raise HTTPException(409,'Pinned encoder/decoder unavailable')
    tools.production_inspect(raw)
    if not tools.production_decode(raw).get('passed'):raise HTTPException(409,'Independent decode failed')
    changed=db.query(VoicePlaybackTest).filter_by(id=run.id,status='REVIEW').update(
        {'status':'RUNNING','confirmed_at':datetime.utcnow()},synchronize_session=False)
    db.commit();db.refresh(run)
    if not changed:return run
    trace=voice_trace.new('owner_candidate_actual_production_path')
    trace.update(run_id=run.id,idempotency_key='candidate:'+run.id+':P',source=run.evidence['source'])
    trace['stages']['immutable_snapshot']=voice_trace.fingerprint(raw)
    claimed=db.query(VoicePlaybackVariant).filter_by(id=variant.id,status='NOT_STARTED').update(
        {'status':'UPLOADING','audio_trace':voice_trace.snapshot(trace)},synchronize_session=False)
    if not claimed:db.rollback();raise HTTPException(409,'Candidate already claimed')
    run.evidence=experiment.audit(db,run.evidence,user,'CANDIDATE_AUTHORIZED',message_count=1)
    db.commit();db.refresh(variant)
    def boundary():
        if datetime.utcnow()-run.created_at>timedelta(minutes=15):raise ValueError('Authorization expired')
        if owner.binding(configuration())!=run.recipient_hash:raise ValueError('Configuration changed')
        owner.window(db,recipient)
    try:
        with voice_trace.capture(trace):
            boundary();media=whatsapp_service.upload_whatsapp_audio(raw)
            variant.media_id=media;variant.audio_trace=voice_trace.snapshot(trace)
            if not media:variant.status='UPLOAD_FAILED';db.commit()
            else:
                variant.status='SENDING';db.commit();boundary()
                outcome,wid=whatsapp_service.send_whatsapp_audio(recipient,media,voice=run.evidence['delivery_mode']=='native',return_outcome=True)
                variant.status=outcome;variant.whatsapp_message_id=wid;variant.audio_trace=voice_trace.snapshot(trace);db.commit()
    except Exception:
        db.rollback();db.refresh(variant);variant.status='UNKNOWN';variant.audio_trace=voice_trace.snapshot(trace);db.commit()
    run.status='SUBMITTED' if variant.status=='SENT' else 'INCOMPLETE'
    run.evidence=experiment.audit(db,run.evidence,user,'CANDIDATE_SEND_FINISHED',status=run.status)
    db.commit();return run


def results(db,user,run_id,result,platform):
    run=get_run(db,user,run_id)
    if run.status in {'REVIEW','RUNNING','OFFLINE_ONLY'}:raise HTTPException(409,'No completed candidate attempt')
    if result not in {'plays','cannot_play','cannot_download','cannot_share','not_tested'} or platform not in {'Android','iPhone','Web/Desktop','Other'}:
        raise HTTPException(422,'Select valid phone results')
    variant=db.query(VoicePlaybackVariant).filter_by(run_id=run.id,variant='P').one()
    if result!='not_tested' and variant.status!='SENT':raise HTTPException(409,'Candidate was not accepted')
    run.playback={'platform':platform,'P':{'result':result,'file':run.evidence['variants']['P']['bytes'],
        'media_id':variant.media_id,'whatsapp_message_id':variant.whatsapp_message_id,'mode':run.evidence['delivery_mode']}}
    run.results_by=user.id;run.results_at=datetime.utcnow()
    run.evidence=experiment.audit(db,run.evidence,user,'CANDIDATE_PHONE_RESULT',result=result,platform=platform)
    db.commit();return run


def close(db,user,run_id):
    run=get_run(db,user,run_id)
    changed=db.query(VoicePlaybackTest).filter(VoicePlaybackTest.id==run.id,VoicePlaybackTest.status.in_(
        ['REVIEW','SUBMITTED','INCOMPLETE','CLOSED'])).update({'status':'CLOSED'},synchronize_session=False)
    db.commit();db.refresh(run)
    if not changed:raise HTTPException(409,'Investigate the running candidate before cleanup')
    failures=0
    for name in (run.storage_name,run.evidence['variants']['P']['storage_name']):
        try:storage_path(name).unlink(missing_ok=True)
        except OSError:failures+=1
    run.evidence=experiment.audit(db,run.evidence,user,'CANDIDATE_CLEANUP',failed_file_count=failures)
    run.evidence={**run.evidence,'snapshots_deleted':failures==0};db.commit()
    if failures:raise HTTPException(503,'Candidate cleanup incomplete; run remains closed to sends')
    return run
