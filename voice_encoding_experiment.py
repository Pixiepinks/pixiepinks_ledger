"""Same-source owner experiment, integrated with the existing durable A/B audit."""
from copy import deepcopy
from datetime import datetime, timedelta
import hmac
import json
import os
import re
import threading
from uuid import uuid4

import av
from fastapi import HTTPException
from models import SavedVoice, VoicePlaybackTest, VoicePlaybackVariant, BotAuditEvent
from saved_voice_service import _read_file, _read_original, write_audio, storage_path
import voice_playback_test as owner
import voice_diagnostic_tools as tools
import voice_trace
import whatsapp_service

KIND = 'same_source_encoding'
PREPARE_SLOTS = threading.BoundedSemaphore(1)
LABELS = ('A','B','C')
PROFILE_C = {'encoder':'independent FFmpeg/libopus','rate':48000,'channels':1,'bitrate':32000,
    'application':'voip','frame_duration_ms':20,'vbr':'on','threads':1}


def configuration():
    if os.getenv('VOICE_ENCODING_TEST_ENABLED','').lower() != 'true':
        raise HTTPException(409,'Same-source encoding experiments are disabled')
    return owner.configuration()


def get_run(db, user, run_id):
    run = owner.get_run(db,user,run_id)
    if run.evidence.get('kind') != KIND:
        raise HTTPException(409,'Use the existing A/B workflow for this recording')
    return run


def audit(db, evidence, user, action, **fields):
    db.add(BotAuditEvent(action='VOICE_ENCODING_'+action,actor='administrator:'+str(user.id),
        summary=json.dumps({'run_id':evidence.get('experiment_id'),**fields})))
    value = deepcopy(evidence)
    actions = list(value.get('actions',[]))
    actions.append({'administrator_id':user.id,'at_utc':datetime.utcnow().isoformat(),'action':action,**fields})
    value['actions'] = actions
    return value


def safe_evidence(run):
    value = deepcopy(run.evidence)
    for variant in value.get('variants',{}).values():
        variant.pop('storage_name',None)
    return value


def prepare(db, user, voice_id, revision, typed_recipient, confirmed):
    owner.authorize(user)
    recipient = configuration()
    if not confirmed or not re.fullmatch(r'[1-9][0-9]{7,14}',typed_recipient) or not hmac.compare_digest(recipient,typed_recipient):
        raise HTTPException(400,'Verify the configured owner recipient and source authorization')
    owner.window(db,recipient)
    if not PREPARE_SLOTS.acquire(blocking=False):
        raise HTTPException(429,'An encoding comparison is already processing; try again shortly')
    stored=[]
    try:
        voice=db.get(SavedVoice,voice_id)
        if not voice or not voice.active or voice.revision!=revision or voice.processing_status!='READY':
            raise HTTPException(409,'Choose a current ready recording')
        normalized=_read_original(voice)  # Integrity only; codec work is bounded in children.
        raw=_read_file(voice.original_storage_name) if voice.original_storage_name else normalized
        tools.inspect_source(raw)
        source=voice_trace.fingerprint(raw)
        declared=(voice.audio_diagnostics or {}).get('source',{}).get('sha256')
        if declared and voice.original_storage_name and declared!=source['sha256']:
            raise HTTPException(409,'Retained source integrity does not match its upload record')
        runtime=tools.runtime()
        evidence=voice_trace.new('owner_same_source_encoding')
        evidence.update(kind=KIND,experiment_id=uuid4().hex,source=source,source_origin='retained_original' if voice.original_storage_name else 'normalized_fallback',
            independent_runtime=runtime,variants={},message_type={'type':'audio','voice':'omitted for every variant'},
            aac_control={'enabled':False,'reason':'Current official Meta AAC/M4A audio contract could not be verified; no AAC sends.'})
        source_name=write_audio(raw,original=True);stored.append(source_name)
        candidates=[('A','Historical-style Opus',lambda:tools.encode_pyav(raw,'historical'),
            {'encoder':'PyAV/libopus','pyav_version':av.__version__,'libraries':av.library_versions,'resampler':'fltp',
             'rate':48000,'channels':1,'bitrate':'unspecified','application':'unspecified','frame_duration_ms':'unspecified'}),
            ('B','Current-style Opus',lambda:tools.encode_pyav(raw,'current'),
            {'encoder':'PyAV/libopus','pyav_version':av.__version__,'libraries':av.library_versions,'resampler':'flt',
             'rate':48000,'channels':1,'bitrate':32000,'application':'voip','frame_duration_ms':20,'vbr':'on'}),
            ('C','Independent FFmpeg reference',lambda:tools.encode(raw),{**PROFILE_C,'runtime':runtime})]
        for label,title,encode,parameters in candidates:
            value={'label':title,'source':source,'parameters':parameters,'mime_type':'audio/ogg','extension':'.ogg','eligible':False}
            try:
                if label=='C' and not runtime.get('verified'):
                    raise ValueError('Pinned encoder unavailable')
                encoded=encode()
                info=tools.inspect_variant(encoded)
                independent=tools.decode(encoded) if runtime.get('verified') else {'passed':False,'reason':'Pinned independent decoder unavailable'}
                independent['duration_matches']=abs(independent.get('decoded_duration_seconds',-1)-info['validation']['duration_seconds'])<=.05
                name=write_audio(encoded);stored.append(name)
                value.update(storage_name=name,bytes=voice_trace.fingerprint(encoded),audio=info,independent=independent,
                    valid=True,eligible=bool(independent.get('passed') and independent['duration_matches'] and not info['warnings']))
            except (ValueError,OSError,av.error.FFmpegError):
                value['error']='Encoding or independent validation failed; this variant cannot be sent'
            evidence['variants'][label]=value
        eligible=all(evidence['variants'][label]['eligible'] for label in LABELS)
        run=VoicePlaybackTest(id=evidence['experiment_id'],staff_id=user.id,voice_id=voice.id,voice_revision=voice.revision,
            storage_name=source_name,recipient_hash=owner.binding(recipient),status='REVIEW' if eligible else 'OFFLINE_ONLY',
            evidence=audit(db,evidence,user,'PREPARED',eligible=eligible,message_count=3))
        db.add(run);db.flush()
        for label in LABELS:
            key='encoding:'+run.id+':'+label
            db.add(VoicePlaybackVariant(run_id=run.id,variant=label,status='NOT_STARTED',
                audio_trace={'idempotency_key':key,'source':source,'immutable_file':evidence['variants'][label].get('bytes')}))
        db.commit()
        return run
    except (ValueError,av.error.FFmpegError):
        db.rollback()
        for name in stored:storage_path(name).unlink(missing_ok=True)
        raise HTTPException(422,'Use an intact owner recording between 0.25 and 30 seconds') from None
    except Exception:
        db.rollback()
        for name in stored:storage_path(name).unlink(missing_ok=True)
        raise
    finally:
        PREPARE_SLOTS.release()


def live_boundary(db,run,recipient):
    if datetime.utcnow()-run.created_at>timedelta(minutes=15):
        voice_trace.record('authorization',{'allowed':False,'reason':'expired'})
        raise ValueError('Authorization expired')
    try:current=configuration()
    except HTTPException:
        voice_trace.record('authorization',{'allowed':False,'reason':'diagnostic_configuration_disabled'})
        raise
    if owner.binding(current)!=run.recipient_hash:
        voice_trace.record('authorization',{'allowed':False,'reason':'configuration_changed'})
        raise ValueError('Configuration changed')
    try:owner.window(db,recipient)
    except HTTPException:
        voice_trace.record('authorization',{'allowed':False,'reason':'service_window_closed'})
        raise


def execute(db,user,run_id,confirmed,count):
    run=get_run(db,user,run_id)
    recipient=configuration()
    if not confirmed or count!=3:
        raise HTTPException(400,'Confirm the owner destination, authorization, open window and exactly three messages')
    if not hmac.compare_digest(run.recipient_hash,owner.binding(recipient)):
        raise HTTPException(409,'Owner/business configuration changed; prepare a new comparison')
    if run.status!='REVIEW':return run
    if any(v.status!='NOT_STARTED' for v in db.query(VoicePlaybackVariant).filter_by(run_id=run.id)):
        raise HTTPException(409,'A variant already has a durable attempt; successful or ambiguous variants cannot be retried')
    if datetime.utcnow()-run.created_at>timedelta(minutes=15):
        raise HTTPException(409,'Review expired; prepare a new comparison')
    owner.window(db,recipient)
    runtime=tools.runtime()
    if not runtime.get('verified') or runtime.get('binary_sha256')!=run.evidence['independent_runtime'].get('binary_sha256'):
        raise HTTPException(409,'The reviewed independent decoder is unavailable or changed')
    if voice_trace.fingerprint(_read_file(run.storage_name))!=run.evidence['source']:
        raise HTTPException(409,'Source snapshot integrity changed; nothing sent')
    raws={}
    # Validate EVERY snapshot before the first upload; no partial send due to a
    # corrupt later file. Re-decode immutable outputs with the pinned executable.
    for label in LABELS:
        info=run.evidence['variants'][label]
        if not info.get('eligible'):raise HTTPException(409,'All three variants must pass offline validation')
        raw=_read_file(info['storage_name'])
        if voice_trace.fingerprint(raw)!=info['bytes']:raise HTTPException(409,'Variant integrity changed; nothing sent')
        try:tools.inspect_variant(raw)
        except ValueError:raise HTTPException(422,'Variant failed bounded validation; nothing sent') from None
        result=tools.decode(raw)
        if not result.get('passed') or abs(result.get('decoded_duration_seconds',-1)-info['audio']['validation']['duration_seconds'])>.05:
            raise HTTPException(409,'Independent decode failed; nothing sent')
        raws[label]=raw
    changed=db.query(VoicePlaybackTest).filter_by(id=run.id,status='REVIEW').update(
        {'status':'RUNNING','confirmed_at':datetime.utcnow()},synchronize_session=False)
    db.commit();db.refresh(run)
    if not changed:return run
    run.evidence=audit(db,run.evidence,user,'AUTHORIZED_THREE_SENDS',message_count=3)
    db.commit()
    for label in LABELS:
        variant=db.query(VoicePlaybackVariant).filter_by(run_id=run.id,variant=label).one()
        trace=voice_trace.new('owner_same_source_encoding_'+label)
        trace.update(run_id=run.id,idempotency_key='encoding:'+run.id+':'+label,source=run.evidence['source'])
        trace['stages']['immutable_snapshot']=voice_trace.fingerprint(raws[label])
        claimed=db.query(VoicePlaybackVariant).filter_by(id=variant.id,status='NOT_STARTED').update(
            {'status':'UPLOADING','audio_trace':voice_trace.snapshot(trace)},synchronize_session=False)
        if not claimed:
            db.rollback();break
        db.add(BotAuditEvent(action='VOICE_ENCODING_VARIANT_ADMITTED',actor='administrator:'+str(user.id),
            summary=json.dumps({'run_id':run.id,'variant':label,'idempotency_key':trace['idempotency_key']})))
        db.commit();db.refresh(variant)
        try:
            with voice_trace.capture(trace):
                live_boundary(db,run,recipient)
                media=whatsapp_service.upload_whatsapp_audio(raws[label])
                variant.media_id=media;variant.audio_trace=voice_trace.snapshot(trace)
                if not media:
                    variant.status='UPLOAD_FAILED';db.commit();break
                variant.status='SENDING';db.commit()
                live_boundary(db,run,recipient)
                outcome,wid=whatsapp_service.send_whatsapp_audio(recipient,media,voice=False,return_outcome=True)
                variant.status=outcome;variant.whatsapp_message_id=wid;variant.audio_trace=voice_trace.snapshot(trace)
                db.commit()
                if outcome!='SENT':break
        except Exception:
            db.rollback();db.refresh(variant)
            variant.status='UNKNOWN';variant.audio_trace=voice_trace.snapshot(trace);db.commit();break
    statuses=[v.status for v in db.query(VoicePlaybackVariant).filter_by(run_id=run.id)]
    run.status='SUBMITTED' if statuses==['SENT']*3 else 'INCOMPLETE'
    run.evidence=audit(db,run.evidence,user,'SEND_SEQUENCE_STOPPED',status=run.status)
    db.commit();return run


def results(db,user,run_id,values,platform):
    run=get_run(db,user,run_id)
    if run.status in {'REVIEW','OFFLINE_ONLY','RUNNING'}:raise HTTPException(409,'No completed send sequence to evaluate')
    if platform not in {'Android','iPhone','Web/Desktop','Other'}:raise HTTPException(422,'Select a client platform')
    allowed={'plays','cannot_play','cannot_download','cannot_share','not_tested'}
    variants={v.variant:v for v in db.query(VoicePlaybackVariant).filter_by(run_id=run.id)}
    if set(values)!=set(LABELS) or any(value not in allowed for value in values.values()):raise HTTPException(422,'Choose a result for each exact variant')
    if any(values[label]!='not_tested' and variants[label].status!='SENT' for label in LABELS):raise HTTPException(409,'Only accepted messages can have a phone result')
    evidence={label:{'result':values[label],'file':run.evidence['variants'][label].get('bytes'),
        'media_id':variants[label].media_id,'whatsapp_message_id':variants[label].whatsapp_message_id} for label in LABELS}
    run.playback={'variants':evidence,'platform':platform}
    run.results_by=user.id;run.results_at=datetime.utcnow()
    run.evidence=audit(db,run.evidence,user,'PLAYBACK_RECORDED',results=values,platform=platform)
    db.commit();return run


def close(db,user,run_id):
    run=get_run(db,user,run_id)
    # CAS closes review before deleting files; a sending worker cannot race it.
    changed=db.query(VoicePlaybackTest).filter(VoicePlaybackTest.id==run.id,VoicePlaybackTest.status.in_(
        ['REVIEW','OFFLINE_ONLY','SUBMITTED','INCOMPLETE','CLOSED'])).update({'status':'CLOSED'},synchronize_session=False)
    db.commit();db.refresh(run)
    if not changed:raise HTTPException(409,'Running/ambiguous sequence must finish before closing')
    run.evidence=audit(db,run.evidence,user,'CLEANUP_REQUESTED')
    db.commit()
    names=[run.storage_name]+[v.get('storage_name') for v in run.evidence['variants'].values()]
    failures=0
    for name in names:
        try:
            if name:storage_path(name).unlink(missing_ok=True)
        except OSError:failures+=1
    value=audit(db,run.evidence,user,'SNAPSHOT_DELETE_FAILED' if failures else 'SNAPSHOTS_DELETED',failed_file_count=failures)
    value['snapshots_deleted']=not failures;run.evidence=value
    db.commit()
    if failures:raise HTTPException(503,'Experimental cleanup incomplete. Fix storage permissions and retry close; the test remains closed to sends.')
    return run


def interpretation(playback):
    values={k:v.get('result') for k,v in (playback or {}).get('variants',{}).items()}
    if values.get('A')=='plays' and values.get('B')=='cannot_play':
        return 'Historical works/current fails: evidence of a profile difference; mode alone is not isolated.'
    if values.get('A')=='plays' and values.get('B')=='plays':
        return 'Both PyAV profiles play: investigate recording-specific and historical conditions; no regression fix is proven.'
    if values.get('C')=='plays' and values.get('A')=='cannot_play' and values.get('B')=='cannot_play':
        return 'Independent output plays/both PyAV outputs fail: investigate muxing, encoder configuration and finalization.'
    if all(values.get(k)=='cannot_play' for k in LABELS):
        return 'All fail: investigate common media upload, MIME, provider processing and client behavior; do not deploy a historical-profile fix.'
    if any(value in {'cannot_download','cannot_share'} for value in values.values()):
        return 'A download/share outcome was recorded; it alone does not establish whether that variant can play. Check playback separately.'
    return 'Awaiting comparable actual phone results. File validity, decoding, API acceptance, delivery and phone playback are separate outcomes.'
