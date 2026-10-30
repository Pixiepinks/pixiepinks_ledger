# Voice production investigation — evidence, not a playback fix

## Verified and unverified observations

Repository verification: fetched `origin/main` is `a9a027c`, the merge of PR #97,
and contains `22b05f8`. This does **not** verify the deployed Railway artifact.
The checked-in web Procfile starts `uvicorn main:app --host 0.0.0.0 --port $PORT`.
`nixpacks.toml` uses the PyAV binary wheel and C++ runtime. The separate
`railway-learning.toml` starts `python -m learning_worker tick`, not the web server.
Railway's actual source branch, root/config path, start-command overrides, active
commit and replica/process inventory require owner/control-plane inspection.

The owner's supplied production access logs show on 30 October 2026:

| Event | UTC | Sri Lanka time | What it establishes |
|---|---|---|---|
| Application startup completed | 10:05:03 | 15:35:03 | A Uvicorn web process started |
| New Saved Voice upload returned 201 | 10:10:55 | 15:40:55 | An upload request completed |
| Saved Voices send returned 200 | 10:11:30 | 15:41:30 | The CRM endpoint returned a response |
| CRM media 2896 returned 206 | 10:11:30 | 15:41:30 | The CRM served a byte range for its preview |
| Webhooks returned 200 | 10:11:33 | 15:41:33 | Webhook HTTP processing completed; payload/status is absent |

The newer `saved_voices.js?v=audio-validation-2` asset was requested. This supports
that the frontend was updated; it is not proof of the backend Git revision.
Earlier 409s and a progress 404 are distinct rejected/unclaimed attempts. Their
response bodies are needed to explain them, and they do not establish the cause
of the later WhatsApp playback failure. The send endpoint may also return HTTP
200 with a FAILED/UNKNOWN result: its JSON and stored status matter. Likewise,
a 206 CRM preview is not proof about Meta's stored media or phone playback.
Uvicorn INFO lines on Railway's `[err]` stream are not themselves exceptions.
Multiple internal proxy IPs are not proof of multiple old web processes.

Known owner-case lookup: Saved Voice **10**, local message **2896**, Meta media
**2062516331063243**. The supplied DB row identifies send-attempt **54**, revision
**1**, status **SENT**, delivery status **delivered**, and no recorded delivery
error. These are owner-supplied DB observations; phone playback is still failing. The owner also supplied a WhatsApp message ID; compare it
privately against the matched attempt. Message 2896 is not assumed to be the
send-attempt ID. The actual production commit, exact Alembic revision, deployed
self-check output, historical multipart bytes and Meta media bytes remain **not independently verified
by this workspace**. The supplied row does report delivered with no error; that
is the last confirmed stage in the supplied evidence, not proof of valid media. The only confirmed
playback observation is the owner's report that WhatsApp cannot play the audio.
No exact server failure stage or audio interoperability root cause is established.

A separate verified observability defect was reproduced: Uvicorn's default
logging configuration enables Uvicorn loggers but does not enable application
root INFO. The previous `audio_service`/`whatsapp_service` INFO diagnostics can
therefore be absent even though access logs appear. A subprocess regression
reproduces this. The correction enables only `pixiepinks.voice_evidence`, with a
dedicated handler and no root INFO/DEBUG change. It logs selected hashes, lengths,
formats, internal trace/media/message IDs and numeric codes; no audio, filenames,
transcripts, customer phone numbers, tokens, signed URLs or provider error text.
This is a logging correction, **not a confirmed correction of phone playback**.

## First: probe the existing deployment without deploying another voice change

Inside the **running web service** (Railway SSH/console), inspect the Active
Deployment's exact source commit in Railway and run:

```sh
python -m alembic current
python -m audio_service --check
```

Do not substitute local checkout results or GitHub merge status. The expected
previous schema is at least `20261030_19`; the self-check must run in that web
container's Python environment. Global AI must remain OFF. Do not dump variables,
connection strings or credentials. Confirm that the web service is not using the
learning cron configuration, and inventory/drain old releases before relying on
new code. Existing active processes and worker overrides cannot be established
from the repository alone.

`voice_owner_probe.py` is deliberately compatible with the existing
22b05f8/schema-19 application and selects only schema-19 columns. It may be copied
as a single file into `/tmp` without replacing or restarting the running web app.
From the app directory, use:

```sh
PYTHONPATH=. python /tmp/voice_owner_probe.py \
  --message-id 2896 --recording-id 10 --media-id 2062516331063243
```

It verifies the DB relationship before inspecting the current stored bytes. It
prints only selected runtime/schema/attempt/validation/hash fields. To retrieve
**that already-failed owner test media**, without sending another message:

```sh
PYTHONPATH=. python /tmp/voice_owner_probe.py \
  --message-id 2896 --recording-id 10 --media-id 2062516331063243 \
  --retrieve-meta
```

This uses the already-configured token in memory, GETs through the app's existing
trusted-host media-download helper, prints SHA-256/length/MIME/validation results,
and neither exports audio nor prints URLs/tokens/phone numbers. It checks that
all three supplied IDs match before any Meta request. It is read-only: no
migration, conversion of production recordings, settings update, upload or send.
If media is expired/unavailable, report that limitation; do not manufacture a
hash match. Current file hashes are not retrospective proof of historical upload
bytes, even when the recording revision matches the send attempt.

## Instrumentation release: running build and full future-boundary evidence

This branch adds diagnostics without changing `audio_service.py`, its encoder
options or the audio send payload/voice flag. Nixpacks writes a non-secret
`build_revision.json` during build, containing commit provenance and code hashes.
At startup, `VOICE_EVIDENCE` reports artifact/platform commit, file fingerprints,
normalization version and a per-process identifier. Missing provenance remains
unknown. These are runtime/artifact reports, not cryptographic control-plane
attestation: compare them against Railway's Active Deployment and source commit.
Alternative Docker/start/build overrides must also generate provenance or will
honestly report it missing.

After deploying this **diagnostic** release to all sending web instances, startup
runs additive migration `20261030_20`: only nullable `audio_trace` JSON columns on
SavedVoiceSend and WhatsAppManualSend. Existing claims, recordings, originals,
Global AI generation/state, learning configuration and accounting are unchanged.
It does not backfill invented evidence, reconvert recordings, send backlog or
perform any automatic test message. Check the Nixpacks build phase did run; the
commit displayed by a build must not be inferred from a successful git push.

ADMIN-only, no-cache read-only page:

`/crm/bot-management/voice-diagnostics?message_id=2896&recording_id=10`

A link is available in Bot Management → Settings. Other staff cannot access the
page. It reports the actual serving process, synthetic self-check and library
versions, current schema, Global AI state, safe recent attempt IDs, and the
matched attempt. Loading the page performs no Meta requests or settings writes. After selecting
attempt 54 (or using the message/recording lookup), an administrator may explicitly
confirm this is the owner test and click **Inspect existing Meta media — no send**.
That action performs GET/download comparisons only; it cannot send a message.
It is protected by administrator authorization, CSRF and an explicit owner-test
confirmation. This browser option avoids needing a Railway shell for the extended
read-only inspection after the diagnostic release.
It shows separate browser and Saved Voices routes and never assumes a current
file establishes an earlier send's bytes. Repeated observations can identify
mixed serving process/deployment IDs; missing/404 reports from older releases
remain a reason for control-plane investigation, not an invented diagnosis.

Equivalent read-only commands inside the running service:

```sh
python -m voice_production_diagnostics runtime
python -m voice_production_diagnostics trace --message-id 2896 --recording-id 10
python -m voice_production_diagnostics trace --message-id 2896 --recording-id 10 --retrieve-meta
```

The extended Meta retrieval also compares metadata SHA-256 (hex/base64), size and
MIME with bounded downloaded bytes. It accepts only trusted HTTPS media hosts,
forwards credentials to no other host, follows no redirects, and never outputs a
signed URL or audio. It validates downloaded OGG/Opus but makes no phone-playback
claim. Current-file equality and historical-upload equality are reported
separately. Retrieval is opt-in and GET-only; there is no sending command. The confirmed administrator UI action uses this same GET-only retrieval.

New Saved Voices attempts snapshot the recording/revision/source metadata,
normalized-record SHA/length, exact storage-read SHA/length, validation SHA/length,
and actual multipart file-part SHA/length. New browser-recorded attempts snapshot
the original browser upload and normalized in-memory bytes. That route does not
persist its audio file locally; do not confuse it with Saved Voices storage.
Both capture the configured Graph version, hashed business phone ID at upload and
send, returned media ID, actual audio-object/voice-flag fields, returned WhatsApp
message ID, numeric HTTP/Meta failures and delivery status. They preserve the
existing idempotency claims and do not automatically retry uncertain sends.

The low-level upload/send trace is captured at the actual request construction,
not inferred from a file extension. Tests parse the serialized HTTPX multipart
body and compare its decoded file-part bytes to the recorded hashes. A mismatch
is reported at the first observed boundary; absent stages are explicitly
incomplete, including injected/alternate senders that did not execute this
transport. Old attempts have no historical hash trace. They are not silently
filled from today's file. New future evidence requires a separately authorized
owner test, not an unsolicited customer send.

## Meta contract check and controlled A/B plan — not executed

Current request construction inspected in code:

- POST `https://graph.facebook.com/{configured-version}/{business-phone-id}/media`
- Multipart file contains actual validated bytes and `audio/ogg`; messaging_product
  is whatsapp, media type is audio/ogg; returned ID is used rather than a URL.
- POST the same configured business phone's `/messages` endpoint with type audio.
  The existing native-voice object is `{id: MEDIA_ID, voice: true}`; ordinary
  audio omits voice. The normal JSON recipient is deliberately excluded from logs.
- No SavedVoice-level media-ID cache is used; idempotent replay returns the old
  attempt status rather than uploading/sending again. Current-file preview and
  Meta upload read the same validated bytes in the instrumented Saved Voices path.

Attempts to retrieve the current public Meta audio-message documentation failed
from this workspace with both proxy and direct connections. Consequently **live
contract verification is still pending**, not asserted. Review current Meta docs
for the configured Graph version from an environment with access and compare the
recorded payload. Do not remove/add the voice flag speculatively.

A/B sends are **not authorized by supplying IDs or logs**. Obtain explicit owner
approval for exactly two messages to their verified test conversation/number first.
No executable send command or experiment endpoint is included in this release.
Then the controlled implementation/operation should:

1. Verify the selected recipient is the owner's approved test, the 24-hour service
   window is open, Global AI remains OFF, and recording 10's selected revision and
   SHA/length are fixed. Retrieve/hash the existing media or upload the exact
   validated bytes once if the media has expired. Stop on an unexplained mismatch.
2. Prefer the same Meta media ID and validated bytes for both arms, so only the
   audio-object flag varies. Claim two durable existing send-attempt records with
   distinct scoped keys such as `voice-ab-<experiment>-A` and
   `voice-ab-<experiment>-B`. Record IDs, hashes, mode and results; do not call a
   bare sender without durable idempotency. Any retry of an uncertain arm must
   only read its recorded result, never automatically send again.
3. A: ordinary audio (`voice=False`, no voice key). B: native voice
   (`voice=True`). Send exactly once per approved arm. Do not alter the default
   sending mode for other staff/customers. The current media and token must belong
   to the configured business phone; compare hashed phone-ID consistency in traces
   and confirm the actual business account privately in Railway/Meta.
4. On the owner's actual phone, play each complete recording, seek and replay it.
   Record A/B success/warning, phone OS/WhatsApp version, internal attempt IDs,
   media/message IDs and numeric delivery codes. A message ID, delivered/read
   webhook, waveform or CRM/browser playback alone is insufficient.

| Owner result | Next investigation; not an automatic conclusion |
|---|---|
| A plays, B fails | Voice-note-specific payload/Graph contract/client compatibility |
| Both fail | Actual downloaded bytes, Meta processing/metadata, API account/parameters and storage |
| Both play | Reproduction unresolved; compare prior IDs/routes/process/version/cache evidence |
| A fails, B plays | Attachment-specific behavior; verify payload and recipient evidence |

Only implement a delivery correction supported by those observations, then add a
regression for that precise discrepancy. Do not use successful local codec tests
as evidence that the production playback defect is resolved. Keep staff customer
voice use paused until the owner confirms actual playback; leave Global AI OFF.
