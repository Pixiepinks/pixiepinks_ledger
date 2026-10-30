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
        try:service.configuration(); enabled=True
        except HTTPException:enabled=False
        variants = db.query(VoicePlaybackVariant).filter_by(run_id=run.id).order_by(VoicePlaybackVariant.variant).all() if run else []
        evidence = {'run_id':run.id,'administrator_id':run.staff_id,'created_at':run.created_at,'confirmed_at':run.confirmed_at,
            'recording_id':run.voice_id,'recording_revision':run.voice_revision,'evidence':run.evidence,
            'variants':[{k:getattr(v,k) for k in ('variant','status','media_id','whatsapp_message_id','delivery_status','delivery_error_code','audio_trace')} for v in variants],
            'playback':run.playback,'results_by':run.results_by,'results_at':run.results_at} if run else None
        return templates.TemplateResponse('voice_playback_test.html', {'request':request,'user':user,
            'page_title':'Owner voice playback comparison','csrf_token':csrf(request),'enabled':enabled,'run':run,
            'variants':variants,'summary':service.summary(run.playback) if run else '',
            'evidence_json':json.dumps(evidence,indent=2,default=str),
            'voices':db.query(SavedVoice).filter_by(active=True,processing_status='READY',normalization_version=NORMALIZATION_VERSION).order_by(SavedVoice.id).limit(100).all(),
            'recent':db.query(VoicePlaybackTest).filter_by(staff_id=user.id).order_by(VoicePlaybackTest.created_at.desc()).limit(10).all()},
            headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

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
        run=service.get_run(db,user,run_id);raw=_read_file(run.storage_name)
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

    @router.post(BASE + '/meta')
    def meta(request:Request, run_id:str=Form(...), owner_confirm:bool=Form(False), csrf_token:str=Form(...),
             db=Depends(get_db), user=Depends(require_user)):
        guard(request,user,csrf_token)
        run=service.get_run(db,user,run_id)
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
