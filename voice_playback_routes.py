"""No live send occurs on GET, preview, deployment, or playback result updates."""
import hmac
import json
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from models import SavedVoice, VoicePlaybackTest, VoicePlaybackVariant
from audio_service import NORMALIZATION_VERSION
import voice_playback_test as service
from saved_voice_routes import audio_response
from saved_voice_service import _read_file
import voice_trace

BASE = '/crm/bot-management/voice-diagnostics/ab'


def build_router(get_db, require_user, csrf, templates):
    router = APIRouter()

    def guard(request, user, token=None):
        service.authorize(user)
        if token is not None:
            expected = request.session.get('csrf_token', '')
            if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
                raise HTTPException(403, 'Invalid CSRF token')

    def render(request, db, user, run=None, additional=None):
        import voice_encoding_experiment as encoding
        import voice_ffmpeg_owner_test as candidate
        is_candidate=bool(run and run.evidence.get('kind')==candidate.KIND)
        try:candidate.configuration();candidate_enabled=True
        except HTTPException:candidate_enabled=False
        is_encoding=bool(run and run.evidence.get('kind')==encoding.KIND)
        try:encoding.configuration();encoding_enabled=True
        except HTTPException:encoding_enabled=False
        try:service.configuration(); enabled=True
        except HTTPException:enabled=False
        variants = db.query(VoicePlaybackVariant).filter_by(run_id=run.id).order_by(VoicePlaybackVariant.variant).all() if run else []
        evidence = {'run_id':run.id,'administrator_id':run.staff_id,'created_at':run.created_at,'confirmed_at':run.confirmed_at,
            'recording_id':run.voice_id,'recording_revision':run.voice_revision,'evidence':encoding.safe_evidence(run) if is_encoding or is_candidate else run.evidence,
            'variants':[{k:getattr(v,k) for k in ('variant','status','media_id','whatsapp_message_id','delivery_status','delivery_error_code','audio_trace')} for v in variants],
            'playback':run.playback,'results_by':run.results_by,'results_at':run.results_at,'read_only_inspection':additional} if run else additional
        return templates.TemplateResponse('voice_ffmpeg_candidate.html' if is_candidate else 'voice_encoding_test.html' if is_encoding else 'voice_playback_test.html', {'request':request,'user':user,
            'page_title':'Owner voice playback comparison','csrf_token':csrf(request),'enabled':enabled,'run':run,'encoding_enabled':encoding_enabled,'candidate_enabled':candidate_enabled,
            'interpretation':encoding.interpretation(run.playback) if is_encoding else '',
            'variants':variants,'summary':service.summary(run.playback) if run else '',
            'evidence_json':json.dumps(evidence,indent=2,default=str),
            'voices':db.query(SavedVoice).filter_by(active=True,processing_status='READY',normalization_version=NORMALIZATION_VERSION).order_by(SavedVoice.id).limit(100).all(),
            'recent':db.query(VoicePlaybackTest).filter_by(staff_id=user.id).order_by(VoicePlaybackTest.created_at.desc()).limit(10).all()},
            headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    @router.post(BASE + '/encoding/evidence')
    def inspect_experiment(request:Request,run_id:str=Form(...),owner_confirm:bool=Form(False),retrieve_meta:bool=Form(False),
                           offline_remux:bool=Form(False),csrf_token:str=Form(...),db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        import voice_encoding_experiment as encoding
        run=encoding.get_run(db,user,run_id);recipient=service.configuration()
        if not owner_confirm:raise HTTPException(400,'Confirm read-only owner experiment inspection')
        if service.binding(recipient)!=run.recipient_hash:raise HTTPException(409,'Owner/business binding changed')
        if offline_remux and not retrieve_meta:raise HTTPException(400,'Remux requires explicitly requested existing-media retrieval')
        from voice_experiment_evidence import report
        from models import BotAuditEvent
        try:value=report(db,run.id,retrieve=retrieve_meta,remux=offline_remux)
        except (ValueError,OSError):raise HTTPException(422,'Bounded existing-media inspection failed; no sends') from None
        db.add(BotAuditEvent(action='VOICE_ENCODING_EVIDENCE_READ',actor='administrator:'+str(user.id),
            summary=json.dumps({'run_id':run.id,'retrieve_meta':retrieve_meta,'offline_remux':offline_remux})));db.commit()
        return render(request,db,user,run,additional=value)

    @router.post(BASE + '/candidate/prepare')
    def prepare_candidate(request:Request,reference_id:str=Form(...),voice_id:int=Form(...),revision:int=Form(...),owner_number:str=Form(...),
                          owner_confirm:bool=Form(False),mode:str=Form(...),csrf_token:str=Form(...),db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        import voice_ffmpeg_owner_test as candidate
        from audio_service import VoiceAudioError
        try:run=candidate.prepare(db,user,reference_id,voice_id,revision,owner_number,owner_confirm,mode)
        except VoiceAudioError as exc:raise HTTPException(422,str(exc)) from None
        except ValueError:raise HTTPException(422,'Fresh candidate encoding/validation failed; no fallback or send was used') from None
        return render(request,db,user,run)

    @router.post(BASE + '/candidate/run')
    def run_candidate(request:Request,run_id:str=Form(...),confirm_one:bool=Form(False),message_count:int=Form(...),csrf_token:str=Form(...),
                      db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        import voice_ffmpeg_owner_test as candidate
        try:run=candidate.execute(db,user,run_id,confirm_one,message_count)
        except ValueError:raise HTTPException(422,'Candidate preflight failed; nothing sent') from None
        return render(request,db,user,run)

    @router.post(BASE + '/candidate/meta')
    def inspect_candidate(request:Request,run_id:str=Form(...),owner_confirm:bool=Form(False),csrf_token:str=Form(...),
                          db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        import voice_ffmpeg_owner_test as candidate
        import voice_encoding_experiment as encoding
        recipient=candidate.configuration();run=candidate.get_run(db,user,run_id)
        if not owner_confirm:raise HTTPException(400,'Confirm existing owner candidate media retrieval')
        if service.binding(recipient)!=run.recipient_hash:raise HTTPException(409,'Owner/business binding changed')
        from voice_production_diagnostics import retrieve_meta
        variant=db.query(VoicePlaybackVariant).filter_by(run_id=run.id,variant='P').one()
        if not variant.media_id:raise HTTPException(409,'No accepted candidate upload')
        value=dict(variant.audio_trace or {})
        value['meta_retrieval']=retrieve_meta(variant.media_id,run.evidence['variants']['P']['bytes']);variant.audio_trace=value
        run.evidence=encoding.audit(db,run.evidence,user,'CANDIDATE_MEDIA_READ');db.commit()
        return render(request,db,user,run)

    @router.post(BASE + '/candidate/results')
    def result_candidate(request:Request,run_id:str=Form(...),result:str=Form(...),platform:str=Form(...),csrf_token:str=Form(...),
                         db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        import voice_ffmpeg_owner_test as candidate
        return render(request,db,user,candidate.results(db,user,run_id,result,platform))

    @router.post(BASE + '/candidate/close')
    def close_candidate(request:Request,run_id:str=Form(...),confirm_delete:bool=Form(False),csrf_token:str=Form(...),
                        db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        if not confirm_delete:raise HTTPException(400,'Confirm candidate-only snapshot cleanup')
        import voice_ffmpeg_owner_test as candidate
        return render(request,db,user,candidate.close(db,user,run_id))

    @router.get(BASE + '/candidate/{run_id}/audio')
    def preview_candidate(request:Request,run_id:str,db=Depends(get_db),user=Depends(require_user)):
        guard(request,user)
        import voice_ffmpeg_owner_test as candidate
        from models import BotAuditEvent
        run=candidate.get_run(db,user,run_id)
        if run.status=='CLOSED':raise HTTPException(404,'Candidate snapshot closed')
        info=run.evidence['variants']['P'];raw=_read_file(info['storage_name'])
        if voice_trace.fingerprint(raw)!=info['bytes']:raise HTTPException(409,'Candidate integrity mismatch')
        db.add(BotAuditEvent(action='VOICE_ENCODING_CANDIDATE_PREVIEW',actor='administrator:'+str(user.id),summary=json.dumps({'run_id':run.id})));db.commit()
        return audio_response(raw,'audio/ogg',request)

    @router.post(BASE + '/encoding/prepare')
    def prepare_encoding(request:Request, voice_id:int=Form(...), revision:int=Form(...), owner_number:str=Form(...),
                         owner_confirm:bool=Form(False),csrf_token:str=Form(...),db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        import voice_encoding_experiment as encoding
        return render(request,db,user,encoding.prepare(db,user,voice_id,revision,owner_number,owner_confirm))

    @router.post(BASE + '/encoding/run')
    def run_encoding(request:Request,run_id:str=Form(...),confirm_three:bool=Form(False),message_count:int=Form(...),
                     csrf_token:str=Form(...),db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        import voice_encoding_experiment as encoding
        return render(request,db,user,encoding.execute(db,user,run_id,confirm_three,message_count))

    @router.post(BASE + '/encoding/meta')
    def encoding_meta(request:Request,run_id:str=Form(...),owner_confirm:bool=Form(False),csrf_token:str=Form(...),
                      db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        import voice_encoding_experiment as encoding
        recipient=encoding.configuration();run=encoding.get_run(db,user,run_id)
        if not owner_confirm:raise HTTPException(400,'Confirm read-only retrieval of the owner test media')
        if service.binding(recipient)!=run.recipient_hash:raise HTTPException(409,'Owner configuration changed')
        from voice_production_diagnostics import retrieve_meta
        for variant in db.query(VoicePlaybackVariant).filter_by(run_id=run.id).all():
            if variant.media_id:
                value=dict(variant.audio_trace or {})
                value['meta_retrieval']=retrieve_meta(variant.media_id,run.evidence['variants'][variant.variant]['bytes'])
                variant.audio_trace=value
        run.evidence=encoding.audit(db,run.evidence,user,'MEDIA_RETRIEVED')
        db.commit();return render(request,db,user,run)

    @router.post(BASE + '/encoding/results')
    def encoding_results(request:Request,run_id:str=Form(...),a:str=Form(...),b:str=Form(...),c:str=Form(...),platform:str=Form(...),
                         csrf_token:str=Form(...),db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        import voice_encoding_experiment as encoding
        return render(request,db,user,encoding.results(db,user,run_id,{'A':a,'B':b,'C':c},platform))

    @router.post(BASE + '/encoding/close')
    def close_encoding(request:Request,run_id:str=Form(...),confirm_delete:bool=Form(False),csrf_token:str=Form(...),
                       db=Depends(get_db),user=Depends(require_user)):
        guard(request,user,csrf_token)
        if not confirm_delete:raise HTTPException(400,'Confirm deletion of experimental snapshots')
        import voice_encoding_experiment as encoding
        return render(request,db,user,encoding.close(db,user,run_id))

    @router.get(BASE + '/encoding/{run_id}/{variant}/audio')
    def encoding_preview(request:Request,run_id:str,variant:str,db=Depends(get_db),user=Depends(require_user)):
        guard(request,user)
        import voice_encoding_experiment as encoding
        from models import BotAuditEvent
        run=encoding.get_run(db,user,run_id)
        if variant not in encoding.LABELS or run.status=='CLOSED':raise HTTPException(404,'Experimental snapshot unavailable')
        info=run.evidence['variants'][variant]
        if not info.get('storage_name'):raise HTTPException(409,'Variant generation failed')
        raw=_read_file(info['storage_name'])
        if voice_trace.fingerprint(raw)!=info['bytes']:raise HTTPException(409,'Snapshot integrity check failed')
        db.add(BotAuditEvent(action='VOICE_ENCODING_PREVIEW',actor='administrator:'+str(user.id),
            summary=json.dumps({'run_id':run.id,'variant':variant})));db.commit()
        return audio_response(raw,'audio/ogg',request)

    @router.get(BASE)
    def page(request:Request, run_id:str='', db=Depends(get_db), user=Depends(require_user)):
        guard(request,user)
        return render(request,db,user,service.get_run(db,user,run_id) if run_id else None)

    @router.post(BASE + '/prepare')
    def prepare(request:Request, voice_id:int=Form(...), revision:int=Form(...), owner_number:str=Form(...),
                owner_confirm:bool=Form(False), csrf_token:str=Form(...), db=Depends(get_db), user=Depends(require_user)):
        guard(request,user,csrf_token)
        run=service.prepare(db,user,voice_id,revision,owner_number,owner_confirm)
        return render(request,db,user,run)

    @router.get(BASE + '/{run_id}/audio')
    def preview(run_id:str, request:Request, db=Depends(get_db), user=Depends(require_user)):
        guard(request,user)
        run=service.get_run(db,user,run_id)
        if run.evidence.get('kind') in {'same_source_encoding','production_ffmpeg_candidate'}:raise HTTPException(409,'Use a variant preview')
        raw=_read_file(run.storage_name)
        if voice_trace.fingerprint(raw)!=run.evidence['normalized']:raise HTTPException(409,'Snapshot integrity check failed')
        return audio_response(raw,'audio/ogg',request)

    @router.post(BASE + '/run')
    def execute(request:Request, run_id:str=Form(...), confirm_two:bool=Form(False), csrf_token:str=Form(...),
                db=Depends(get_db), user=Depends(require_user)):
        guard(request,user,csrf_token)
        return render(request,db,user,service.execute(db,user,run_id,confirm_two))

    @router.post(BASE + '/results')
    def results(request:Request, run_id:str=Form(...), a:str=Form(...), b:str=Form(...), platform:str=Form(...),
                description:str=Form(''), csrf_token:str=Form(...), db=Depends(get_db), user=Depends(require_user)):
        guard(request,user,csrf_token)
        return render(request,db,user,service.results(db,user,run_id,a,b,platform,description))

    @router.post(BASE + '/pair')
    def pair(request:Request, run_id:str=Form(...), working_route:str=Form(...), working_attempt_id:int=Form(...,ge=1),
             failed_route:str=Form(...), failed_attempt_id:int=Form(...,ge=1), owner_confirm:bool=Form(False),
             csrf_token:str=Form(...), db=Depends(get_db), user=Depends(require_user)):
        guard(request,user,csrf_token)
        recipient=service.configuration()
        run=service.get_run(db,user,run_id)
        if run.evidence.get('kind') in {'same_source_encoding','production_ffmpeg_candidate'}:raise HTTPException(409,'Use the encoding experiment workflow')
        if not owner_confirm:raise HTTPException(400,'Confirm read-only owner media comparison')
        if working_route not in {'saved','browser'} or failed_route not in {'saved','browser'}:
            raise HTTPException(422,'Choose a valid recording route')
        from models import SavedVoiceSend, WhatsAppManualSend, WhatsAppConversation
        # Resolve both references and verify the destination BEFORE any Meta GET.
        for route,attempt_id in [(working_route,working_attempt_id),(failed_route,failed_attempt_id)]:
            attempt=db.get(SavedVoiceSend if route=='saved' else WhatsAppManualSend,attempt_id)
            conversation=db.get(WhatsAppConversation,attempt.conversation_id) if attempt else None
            if not conversation or conversation.phone_number!=recipient:
                raise HTTPException(404,'Both references must belong to the configured owner test recipient')
        from voice_regression_diagnostics import compare_meta_attempts
        import tempfile
        with tempfile.TemporaryDirectory(prefix='voice-reference-pair-') as directory:
            report=compare_meta_attempts(db,working_route,working_attempt_id,failed_route,failed_attempt_id,directory)
        evidence=dict(run.evidence);evidence['reference_pair']=report;run.evidence=evidence
        db.commit()
        return render(request,db,user,run)

    @router.post(BASE + '/offline')
    def offline(request:Request, run_id:str=Form(...), owner_confirm:bool=Form(False), csrf_token:str=Form(...),
                db=Depends(get_db), user=Depends(require_user)):
        guard(request,user,csrf_token)
        service.configuration()  # Existing experimental feature switch also gates offline generation.
        run=service.get_run(db,user,run_id)
        if run.evidence.get('kind') in {'same_source_encoding','production_ffmpeg_candidate'}:raise HTTPException(409,'Use the encoding experiment workflow')
        if not owner_confirm:raise HTTPException(400,'Confirm owner-authorized offline analysis')
        from voice_regression_diagnostics import compare
        from audio_service import VoiceAudioError
        import tempfile
        voice=db.get(SavedVoice,run.voice_id)
        if not voice:raise HTTPException(409,'Original recording is unavailable')
        source=_read_file(voice.original_storage_name or voice.storage_name)
        if voice_trace.fingerprint(source)!=run.evidence['source']:
            raise HTTPException(409,'Retained source differs from this test; original review cannot be reconstructed')
        failed=_read_file(run.storage_name)
        if voice_trace.fingerprint(failed)!=run.evidence['normalized']:
            raise HTTPException(409,'Snapshot integrity check failed')
        try:
            with tempfile.TemporaryDirectory(prefix='voice-offline-') as directory:
                report=compare(source,directory,actual_failed=failed)
        except (ValueError,VoiceAudioError):
            raise HTTPException(422,'Offline analysis requires a valid recording of 0.25 to 30 seconds') from None
        report['reviewed_snapshot']=report.pop('actual_failed')
        report['reviewed_snapshot']['provenance']='Immutable A/B snapshot; failure is not inferred from bytes'
        report['reported_phone_playback']=run.playback
        evidence=dict(run.evidence);evidence['offline_comparison']=report;run.evidence=evidence
        db.commit()
        return render(request,db,user,run)

    @router.post(BASE + '/meta')
    def meta(request:Request, run_id:str=Form(...), owner_confirm:bool=Form(False), csrf_token:str=Form(...),
             db=Depends(get_db), user=Depends(require_user)):
        guard(request,user,csrf_token)
        run=service.get_run(db,user,run_id)
        if run.evidence.get('kind') in {'same_source_encoding','production_ffmpeg_candidate'}:raise HTTPException(409,'Use the encoding experiment workflow')
        if not owner_confirm:raise HTTPException(400,'Confirm read-only Meta retrieval')
        from voice_production_diagnostics import retrieve_meta
        for variant in db.query(VoicePlaybackVariant).filter_by(run_id=run.id).all():
            if variant.media_id:
                evidence=dict(variant.audio_trace or {})
                evidence['meta_retrieval']=retrieve_meta(variant.media_id,run.evidence['normalized'])
                variant.audio_trace=evidence
        db.commit()
        return render(request,db,user,run)

    return router
