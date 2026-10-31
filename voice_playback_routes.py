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

    def render(request, db, user, run=None):
        import voice_encoding_experiment as encoding
        is_encoding=bool(run and run.evidence.get('kind')==encoding.KIND)
        try:encoding.configuration();encoding_enabled=True
        except HTTPException:encoding_enabled=False
        try:service.configuration(); enabled=True
        except HTTPException:enabled=False
        variants = db.query(VoicePlaybackVariant).filter_by(run_id=run.id).order_by(VoicePlaybackVariant.variant).all() if run else []
        evidence = {'run_id':run.id,'administrator_id':run.staff_id,'created_at':run.created_at,'confirmed_at':run.confirmed_at,
            'recording_id':run.voice_id,'recording_revision':run.voice_revision,'evidence':encoding.safe_evidence(run) if is_encoding else run.evidence,
            'variants':[{k:getattr(v,k) for k in ('variant','status','media_id','whatsapp_message_id','delivery_status','delivery_error_code','audio_trace')} for v in variants],
            'playback':run.playback,'results_by':run.results_by,'results_at':run.results_at} if run else None
        return templates.TemplateResponse('voice_encoding_test.html' if is_encoding else 'voice_playback_test.html', {'request':request,'user':user,
            'page_title':'Owner voice playback comparison','csrf_token':csrf(request),'enabled':enabled,'run':run,'encoding_enabled':encoding_enabled,
            'interpretation':encoding.interpretation(run.playback) if is_encoding else '',
            'variants':variants,'summary':service.summary(run.playback) if run else '',
            'evidence_json':json.dumps(evidence,indent=2,default=str),
            'voices':db.query(SavedVoice).filter_by(active=True,processing_status='READY',normalization_version=NORMALIZATION_VERSION).order_by(SavedVoice.id).limit(100).all(),
            'recent':db.query(VoicePlaybackTest).filter_by(staff_id=user.id).order_by(VoicePlaybackTest.created_at.desc()).limit(10).all()},
            headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

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
        if run.evidence.get('kind')=='same_source_encoding':raise HTTPException(409,'Use a variant preview')
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
        if run.evidence.get('kind')=='same_source_encoding':raise HTTPException(409,'Use the encoding experiment workflow')
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
        if run.evidence.get('kind')=='same_source_encoding':raise HTTPException(409,'Use the encoding experiment workflow')
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
        if run.evidence.get('kind')=='same_source_encoding':raise HTTPException(409,'Use the encoding experiment workflow')
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
