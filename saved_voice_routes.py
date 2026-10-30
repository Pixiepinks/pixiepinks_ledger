"""Authenticated Saved Voices management, preview and explicit inbox sends."""
import asyncio
import hashlib
import threading

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import or_, func
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from audio_service import (MAX_AUDIO_BYTES, NORMALIZATION_VERSION, normalize_voice_audio,
    validate_voice_audio, source_audio_info, VoiceAudioError)
from learning_routes import read_body
from models import SavedVoice
from quick_message_service import authorize, MANAGER_ROLES
from saved_voice_service import Edit, Send, metadata, public, read_audio, write_audio, storage_path, send, scoped_attempt, status, prepare

UPLOAD_SLOTS = threading.BoundedSemaphore(2)
SEND_SLOTS = threading.BoundedSemaphore(4)
HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


def audio_response(raw, mime, request):
    headers = {**HEADERS, "Accept-Ranges": "bytes", "Content-Disposition": 'inline; filename="saved-voice.ogg"'}
    value = request.headers.get("range")
    if value:
        try:
            if not value.startswith("bytes=") or "," in value: raise ValueError
            first, last = value[6:].split("-", 1)
            if first:
                start = int(first); end = min(int(last), len(raw) - 1) if last else len(raw) - 1
            else:
                if int(last) <= 0: raise ValueError
                start, end = max(len(raw) - int(last), 0), len(raw) - 1
            if start < 0 or start >= len(raw) or end < start: raise ValueError
        except ValueError:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{len(raw)}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{len(raw)}"
        return Response(raw[start:end + 1], status_code=206, media_type=mime, headers=headers)
    return Response(raw, media_type=mime, headers=headers)


def build_router(get_db, require_user, require_csrf, csrf, templates, get_conversation, upload, deliver):
    router = APIRouter()

    @router.get("/crm/saved-voices")
    def page(request: Request, user=Depends(require_user)):
        authorize(user)
        return templates.TemplateResponse("saved_voices.html", {"request": request, "user": user,
            "page_title": "Saved Voices", "can_manage": user.role in MANAGER_ROLES, "csrf_token": csrf(request)}, headers=HEADERS)

    @router.get("/crm/saved-voices/api")
    def listing(request: Request, q: str = Query("", max_length=100), category: str = Query("", max_length=80),
                inactive: bool = False, offset: int = Query(0, ge=0, le=10000), db=Depends(get_db), user=Depends(require_user)):
        authorize(user, manage=inactive)
        query = db.query(SavedVoice)
        if not inactive: query = query.filter_by(active=True)
        if category: query = query.filter_by(category=category)
        if q:
            query = query.filter(or_(*[getattr(SavedVoice, key).icontains(q, autoescape=True)
                                      for key in ("title", "category", "transcript")]))
        total = query.count()
        rows = query.order_by(SavedVoice.category, SavedVoice.title, SavedVoice.id).offset(offset).limit(50).all()
        categories = db.query(SavedVoice.category).filter(SavedVoice.active.is_(True)).distinct().order_by(SavedVoice.category).limit(100)
        return JSONResponse({"voices": [public(row) for row in rows], "total": total, "offset": offset,
            "limit": 50, "categories": [value for value, in categories], "csrf_token": csrf(request),
            "staff_id": user.id, "can_manage": user.role in MANAGER_ROLES}, headers=HEADERS)

    @router.get("/crm/saved-voices/api/{voice_id}/audio")
    def preview(voice_id: int, request: Request, db=Depends(get_db), user=Depends(require_user)):
        authorize(user)
        row = db.query(SavedVoice).filter_by(id=voice_id).one_or_none()
        if not row: raise HTTPException(404, "Recording not found")
        if row.processing_status != "READY" or row.normalization_version != NORMALIZATION_VERSION:
            raise HTTPException(409, "Prepare and validate this recording before previewing or sending")
        return audio_response(read_audio(row), "audio/ogg", request)

    @router.post("/crm/saved-voices/api/{voice_id}/prepare")
    async def prepare_recording(voice_id: int, request: Request, db=Depends(get_db), user=Depends(require_user)):
        authorize(user); require_csrf(request)
        row=db.query(SavedVoice).filter_by(id=voice_id).one_or_none()
        if not row:raise HTTPException(404,"Recording not found")
        if not UPLOAD_SLOTS.acquire(blocking=False):raise HTTPException(429,"Voice processing is busy")
        try:
            await run_in_threadpool(prepare,db,row,retry=True)
            return JSONResponse({"voice":public(row)},headers=HEADERS)
        except SQLAlchemyError:
            db.rollback();raise HTTPException(503,"Recording processing status could not be saved") from None
        finally:UPLOAD_SLOTS.release()

    @router.post("/crm/saved-voices/api")
    async def create(request: Request, db=Depends(get_db), user=Depends(require_user)):
        authorize(user, manage=True); require_csrf(request)
        if not UPLOAD_SLOTS.acquire(blocking=False): raise HTTPException(429, "Voice upload service is busy")
        form, stored = None, None
        try:
            if not request.headers.get("content-type", "").startswith("multipart/form-data;"):
                raise HTTPException(415, "Upload a recording with its title and transcript")
            body = bytearray()
            async with asyncio.timeout(45):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_AUDIO_BYTES + 65536: raise HTTPException(413, "Recording exceeds 16 MB")
                    body.extend(chunk)
            request._body = bytes(body)
            form = await request.form(max_files=1, max_fields=4)
            if set(form) != {"audio", "title", "category", "transcript", "language"}:
                raise HTTPException(422, "Provide one audio file, title, category, transcript and language")
            audio = form["audio"]
            if not isinstance(audio, UploadFile): raise HTTPException(422, "A recording is required")
            values = metadata({key: form[key] for key in ("title", "category", "transcript", "language")})
            raw = await audio.read(MAX_AUDIO_BYTES + 1)
            mime = audio.content_type or ""
            if mime.split(";", 1)[0].casefold() == "audio/x-m4a": mime = "audio/mp4"
            source = await run_in_threadpool(source_audio_info, raw)
            source["sha256"] = hashlib.sha256(raw).hexdigest()
            normalized, duration = await run_in_threadpool(normalize_voice_audio, raw, mime)
            diagnostics={"source":source,"normalized":validate_voice_audio(normalized)}
            original = await run_in_threadpool(write_audio, raw, original=True)
            stored = await run_in_threadpool(write_audio, normalized)
            row = SavedVoice(**values, storage_name=stored, sha256=hashlib.sha256(normalized).hexdigest(),
                size_bytes=len(normalized), duration_seconds=duration, active=True, revision=1,
                original_storage_name=original, normalization_version=NORMALIZATION_VERSION,
                processing_status="READY", audio_diagnostics=diagnostics,
                created_by=user.username, updated_by=user.username)
            db.add(row); db.commit()
            stored = None
            db.refresh(row)
            return JSONResponse({"voice": public(row)}, status_code=201, headers=HEADERS)
        except VoiceAudioError as exc:
            db.rollback()
            raise HTTPException(422, "Conversion failed: " + str(exc)) from None
        except (ValidationError, ValueError):
            db.rollback()
            raise HTTPException(422, "Use a valid audio recording (up to 16 MB / 10 minutes), title, category and transcript") from None
        except TimeoutError:
            raise HTTPException(408, "Recording upload timed out") from None
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(503, "Could not save the recording details") from None
        finally:
            if form is not None: await form.close()
            # Leave private orphan files after an ambiguous DB commit; deleting
            # here could destroy a recording whose metadata actually committed.
            UPLOAD_SLOTS.release()

    @router.patch("/crm/saved-voices/api/{voice_id}")
    async def edit(voice_id: int, request: Request, db=Depends(get_db), user=Depends(require_user)):
        authorize(user, manage=True); require_csrf(request)
        try:
            values = Edit.model_validate(await read_body(request))
            clean = metadata(values.model_dump(exclude={"active", "revision"}))
        except ValueError: raise HTTPException(422, "Provide valid recording details and the current revision") from None
        changed = db.query(SavedVoice).filter_by(id=voice_id, revision=values.revision).update(
            {**clean, "active": values.active, "updated_by": user.username,
             "updated_at": func.now(), "revision": values.revision + 1}, synchronize_session=False)
        if not changed: db.rollback(); raise HTTPException(409, "Recording changed; refresh before editing")
        db.commit()
        return JSONResponse({"voice": public(db.query(SavedVoice).filter_by(id=voice_id).one())}, headers=HEADERS)

    @router.get("/crm/whatsapp/api/conversations/{cid}/saved-voice-sends/{key}")
    def progress(cid: int, key: str, db=Depends(get_db), user=Depends(require_user)):
        authorize(user); get_conversation(db, cid)
        row = scoped_attempt(db, key, cid, user)
        if not row: raise HTTPException(404, "Voice send not found")
        return JSONResponse(status(row), headers=HEADERS)

    @router.post("/crm/whatsapp/api/conversations/{cid}/send-saved-voice")
    async def send_saved(cid: int, request: Request, db=Depends(get_db), user=Depends(require_user)):
        authorize(user); require_csrf(request)
        try: values = Send.model_validate(await read_body(request))
        except ValidationError: raise HTTPException(422, "Select a saved recording and its current revision") from None
        conversation = get_conversation(db, cid)
        if not SEND_SLOTS.acquire(blocking=False): raise HTTPException(429, "Voice sending is busy; check status before retrying")
        try:
            result = await run_in_threadpool(send, db, user, conversation, values,
                request.headers.get("Idempotency-Key", ""), upload, deliver)
            return JSONResponse(result, headers=HEADERS)
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(503, "Voice send status could not be saved. Check progress before sending again") from None
        finally: SEND_SLOTS.release()
    return router
