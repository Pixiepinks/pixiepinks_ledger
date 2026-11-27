# PixiePinks Monara AI Voice — implementation and controlled release

## Scope and architecture

Built from main `d8e6f6f`, on `feature/monara-ai-voice-system`.
This is a manual staff tool, not an automatic reply system. Generation never sends
anything. Global AI controls, Shopify, POS, accounting, purchasing, loyalty and
customer registration are unchanged. No billable Gemini call, live WhatsApp message,
production migration, variable change or deployment was performed during development.

- Inbox opens a same-origin, conversation-bound Monara panel. Switching conversations
  closes it and invalidates stale UI context. Closing cancels preview polling, **not
  already-reserved provider work**. The dedicated page also serves the library/admin UI.
- A durable request key, staff identity, exact conversation and recipient fingerprint
  reserve one generation before external I/O. A singleton policy row serializes
  PostgreSQL quota admission across workers. Pending/Generating concurrency, per-staff
  daily requests, monthly characters, reserved costs and stored-audio bytes are bounded.
- Generation runs as a FastAPI background task using a fresh DB session and a durable
  PENDING → GENERATING claim. It is not retried or automatically reclaimed after a
  crash. Explicit admin maintenance marks interrupted work failed/review-required;
  it does not issue another billable request. Regenerate is a separately chosen request.
- `google-genai==3.6.1` runs in an isolated child Python environment. Its dependency
  constraints are in `requirements.monara.lock`. This preserves the CRM's existing
  **Pydantic 2.9.2 / httpx 0.27.2** pins; the SDK requires newer Pydantic/httpx2.
  Both environments passed `pip check` during development.
- SDK retries are fixed to one attempt. The worker receives transcript/profile/style
  over stdin and the API key only through its private process environment. Neither
  secrets nor transcripts appear in arguments, files, logs or provider error messages.
  Time, address-space/CPU and output bounds apply. No arbitrary provider instructions
  are executed. There is no prebuilt-voice or alternate-model fallback.
- Valid 24 kHz, signed 16-bit, mono PCM parts are concatenated in order. Empty,
  malformed, interrupted, alternative-candidate, mixed/unsupported MIME and oversized
  responses fail closed. PCM is wrapped as little-endian WAV in memory, matching the
  official Google sample; WAV never becomes a filesystem artifact.
- The existing hash-verified FFmpeg 7.0.2 binary produces MP3 and mono 48 kHz OGG/Opus.
  Existing OGG validation and independent PyAV MP3 decoding verify codec/duration.
  Commands use fixed argv, pipe-only inputs, no shell and no inherited app secrets.
- Private MP3/OGG assets are durable database BLOBs with access/retention checks, not
  ephemeral Railway files or public URLs. Default storage cap is 200 MB, with a
  conservative reservation for in-flight outputs. Listing/usage queries defer BLOBs.
  Adding audio storage to the CRM DB has operational backup/storage implications;
  review capacity before activation. An object-store migration is not required here.
- Explicit library approval shares an asset/transcript with authorized Monara staff.
  No Gemini call is needed to preview or use it. Administrators can archive/reactivate
  unexpired assets. Creator ID, dates, language, voice/style and expiry remain visible.
  Personal audio is staff/conversation-bound until shared or actually sent; sent audio
  follows existing authorized Inbox conversation-view permissions.
- Sending reuses `upload_whatsapp_audio` and `send_whatsapp_audio(..., voice=False,
  return_outcome=True)`, existing credentials/API version, reply-window and ownership
  checks, conversation history and delivery-status updates. The range-response helper
  is reused with an optional safe filename; its existing default remains unchanged.

## Verified API evidence and boundaries

The owner supplied and manually tested the AI Studio export for:

```
model: gemini-3.8-flash-tts
voice_config.voice: voice_xu8nahptj1p9
api version: v1beta
temperature: 1
response_modalities: [audio]
```

The installed isolated SDK was exercised **offline**. It accepts that exact custom
voice and serializes it without a prebuilt voice. Capabilities and package provenance
are recorded in `monara-validation-results.json`.

Official public Google sources read during development:

- [Google SDK types](https://github.com/googleapis/python-genai/blob/v3.6.1/google/genai/types.py):
  `VoiceConfig.voice`, `Part.speech_metadata`, `SpeechMetadata.style` and
  `SpeechConfig.language_code` (ISO 639-1).
- [Google TTS cookbook](https://github.com/google-gemini/cookbook/blob/2e212552b2318092ce1ca66eddced2558cb2522c/quickstarts/Get_started_TTS.ipynb):
  Gemini 3.8 TTS/custom voices, separate text and performance metadata, and PCM WAV
  helper with 24 kHz / 16-bit / mono defaults.

Six configurable non-spoken presets are provided: Friendly customer service (the
owner's supplied default prompt), Professional, Cheerful promotion, Gentle reminder,
Thank you and Order confirmation. Transcript-only is also available. Preset text is
stored as a frozen generation snapshot and sent only as `speech_metadata.style`;
**it is not appended to the spoken transcript**. Tests exercise the actual pinned SDK's
Gemini request converter and prove the text and speechMetadata remain separate.
Editing transcript, language or style invalidates the previous preview approval;
language switching preserves the authored message body. Sinhala/English/Tamil use explicit `si`/`en`/`ta` language codes. Live pronunciation,
style fidelity and custom-voice/model access are not established by offline tests.

The configured `generate_content_stream` call retains the owner's exported transcript
wrapper exactly (`## Transcript:\n` plus the authored message). No voice creation,
listing, replication or deletion API is introduced.

Direct `ai.google.dev` and `developers.facebook.com` documentation access returned
proxy CONNECT 403 under this Codex environment's allowlist. Official Google GitHub
source was accessible. **Current Meta audio documentation was not independently
retrieved here.** Before live sending, an authorized operator must review current
Meta audio upload/send MIME and size limits for the existing configured API version.
The implementation stays within existing validated OGG/Opus upload limits and sends
standard audio attachments, not an unverified native voice-note extension. No claim
of live Meta acceptance or recipient playback is made.

## Approval, disclosure and send fencing

The editor starts empty. A spoken AI disclosure is optional in the application;
staff-authored text is never automatically prefixed or rewritten when changing
language. Staff must listen to and explicitly approve the exact generated audio
for the displayed conversation. Editing requires regeneration and fresh approval.
The CRM continues to label Monara audio as AI-generated internally. This change
is not a determination that disclosure is unnecessary under applicable provider,
platform or legal obligations; the owner must review those obligations before rollout.

Sending has a separate lifecycle: NOT_SENT → UPLOADING → SENDING → SENT,
FAILED or UNKNOWN_REVIEW. Recipient/assignment, authorization, messaging window,
asset identity, activity and expiry are rechecked after upload immediately before
claiming dispatch. The frozen approved recipient is used; it is never retargeted.
A small external-send/assignment race cannot be made atomic with Meta; no claim of
atomic cross-system revocation is made.

Both a unique request key bound to staff/conversation/asset/approval fingerprint and
a unique original asset+conversation intent prevent duplicate sends. Repeating the
same accepted request returns its saved state; a racing/new key is rejected and must
check the original status. SENDING/UNKNOWN_REVIEW is never blindly resent. A crash
after Meta acceptance but before local completion remains fenced for manual review.
Meta message IDs and canonical Inbox delivery/read/failure statuses are retained when
available. SENT means accepted by Meta, not proof of delivery, playback or understanding.

A deliberate repeat of the *same library asset to the same conversation* is blocked
in this release, even after a known SENT result. Different conversations can reuse it
without generation. No unsafe resend override is provided; a future explicitly
supervised repeat-delivery workflow must preserve the original evidence/fences.

## Database migration and audit

Additive revision **20261127_62**, parent **20261126_61**:

- `monara_policy`: default-disabled versioned admin policy, seeded with no grants.
- `monara_jobs`: durable requests, frozen transcript/profile/style, usage reservations,
  audio, library metadata, integrity fingerprint, lifecycle and retention.
- `monara_sends`: durable original manual send claims, identity and outcome.
- `monara_events`: append-only generation, policy, library, send and retention audit.

Unique constraints, foreign keys and lifecycle/query indexes preserve existing
records. PostgreSQL/SQLite audit triggers reject update/deletion. Downgrade refuses
when jobs, send claims or audit history exist. Tests cover repeat upgrade, empty
rollback and retained-history refusal. Existing tables/prices/payments are not altered.

## Exact configuration and installation

Do not apply any of these production changes without owner release approval.
Do not paste API keys in chat, commands, scripts or the repository.

| Variable | Default / approved value | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | absent; install via approved Railway secret handling | Existing authorized AI Studio project/key with access to this voice |
| `MONARA_TTS_ENABLED` | `false` | Generation kill gate |
| `MONARA_TTS_SEND_ENABLED` | `false` | Independent send kill gate |
| `MONARA_TTS_MODEL` | `gemini-3.8-flash-tts` | Exact approved model; overrides fail closed |
| `MONARA_TTS_VOICE_ID` | `voice_xu8nahptj1p9` | Exact approved custom voice |
| `MONARA_TTS_API_VERSION` | `v1beta` | Exact export version |
| `MONARA_TTS_PYTHON` | empty → `/app/.monara-venv/bin/python` | Isolated SDK interpreter; trusted operator setting |
| `MONARA_TTS_TIMEOUT_SECONDS` | `60` | Provider timeout; worker bounds 5–60s, no retry |
| `MONARA_TTS_MAX_CHARACTERS` | `1000` | Per request, hard ceiling 1,000 |
| `MONARA_TTS_MAX_AUDIO_SECONDS` | `90` | PCM/output duration bound, hard ceiling 90 |
| `MONARA_TTS_MAX_AUDIO_BYTES` | `5000000` | Per encoded asset output bound, plus worker ceiling |
| `MONARA_TTS_CONCURRENCY` | `2` | Durable global in-flight limit, bounded 1–4 |
| `MONARA_TTS_RETENTION_DAYS` | `7` | Environment upper bound on policy retention |
| `MONARA_TTS_STORAGE_MAX_BYTES` | `200000000` | Stored + conservative in-flight audio budget; ceiling 1 GB |

The existing Nixpacks build installs the existing hash-pinned FFmpeg dependency.
It additionally builds `.monara-venv` using `requirements.monara.txt` and its locked
constraints. The CRM's main requirements and installed Pydantic/httpx are unchanged.
For an isolated developer setup, not production execution:

```bash
python -m venv .monara-venv
.monara-venv/bin/python -m pip install -r requirements.monara.txt
.monara-venv/bin/python monara_sdk_worker.py --capabilities
```

The last command prints only public configuration/SDK capability data and never calls
Gemini. Do not change `VOICE_FFMPEG_ENCODER_ENABLED` or any existing voice, Global AI,
Shopify, POS, printer or automatic reply setting to activate Monara.

## Administrator and staff operating guide

1. After approved deployment/migration, open an authorized Inbox conversation and
   **Monara AI Voice**. Default-disabled state performs no generation or sending.
2. Admin settings separately grant staff IDs, generation/sending policy, limits,
   retention, styles and default style. Environment gates remain an independent upper
   bound: the form cannot set secrets, Railway variables or Global AI state.
3. Configure a positive monthly reserved-cost budget and a conservative reservation
   per request before policy generation can be enabled. Optional per-1,000 input
   character pricing is an explicit estimate only. No unverified rate is hardcoded.
   Daily/monthly windows are UTC. Cost reservations are not actual provider billing
   or a guaranteed Google billing ceiling; verify pricing and provider-side budget
   controls separately. Failed/uncertain requests keep reservations.
4. Staff choose language/style and type the message including the displayed AI intro.
   Generate, listen with play/pause/seek, inspect duration, and explicitly approve.
   Generate never triggers WhatsApp. Regenerate creates a new billable reservation.
5. Download MP3, explicitly share with the Voice Library, or send the approved audio.
   Personalized library sharing has its own checkbox warning; it is not automatic.
6. On network interruption, use **Check request status**. Session storage keeps only
   staff/conversation-scoped request keys, not transcripts or credentials. Reloads
   recover the original request rather than generate another one.
7. Review send acceptance/delivery separately. UNKNOWN_REVIEW requires inspecting the
   actual existing customer chat/provider evidence. No reset/retry/delete endpoint is
   offered. Do not create another send merely to make a status green.
8. Admin dashboard shows daily/monthly requests, staff IDs, success rate, reported
   tokens, estimated/reserved cost, library send reuse and immutable audit. Actual
   Gemini billing is explicitly unavailable; consult the provider billing system.

## Retention and operations

Assets become unavailable at expiry even before physical purge. The administrator's
**Expire retained audio and fence interrupted jobs** action clears expired audio and
transcripts while preserving usage/fingerprints/audit, and fences stale generation or
send claims without replay. It does not touch existing Saved Voices or other media.
Review/purge regularly (at least daily in the initial rollout). **No automatic purge
scheduler is enabled by deployment**; an approved recurring maintenance arrangement
is an operational prerequisite. Access expiry is immediate, but DB erasure occurs
only when maintenance runs. Jobs still uploading/sending are not erased mid-dispatch;
review interrupted work first and run retention again.

Private and library audio share bounded retention; approving a library item does not
make it permanent. Expired assets cannot be reactivated. In-flight slots or storage
exhaustion fail closed; admin review/maintenance is required, not an automatic retry.
Usage metadata/audit remain after audio expiry and are part of the normal DB backup.
Confirm backup access and retention policies before enabling real customer content.

## Read-only deployment verification

After an approved release, authorized Railway Console:

```bash
cd /app
python -m scripts.validate_monara_readonly
```

This imports no app startup, sets PostgreSQL TRANSACTION READ ONLY and a 15s statement
timeout, verifies migration/policy, effective gates, key *presence*, aggregate queue
states, storage, existing Global AI state, isolated SDK and pinned FFmpeg. It does not
print the key, transcript, customer identity or raw provider errors, and does not
perform provider calls, generation, sending, cleanup or migrations. A successful
check is not live provider compatibility or send authorization.

## Controlled live acceptance — separate approvals

**Production activation remains NO-GO until all owner checks are satisfied.**

1. Review PR/security, additive migration, capacity, backups, retention arrangements,
   official Meta docs for the unchanged API version, and Google model/voice access.
2. Approve deployment with both gates OFF. Verify migration 62 and the read-only
   report. Confirm the existing Global AI OFF state is unchanged; do not toggle it.
3. Approve secret installation through the Railway secret UI without disclosing it.
   Verify only presence/capabilities; do not generate from the diagnostic.
4. Separately approve generation enablement and a tightly limited admin-only policy.
   Authorize one short **billable** synthetic/non-customer preview. Check exact words,
   any authored disclosure, Sinhala pronunciation, Friendly style, duration and MP3/OGG quality.
   Test English/Tamil/other styles only within separately approved budget.
5. Keep sending OFF while validating preview/library permissions and retention.
6. Separately approve sending enablement, one explicit recipient/test conversation
   with a valid customer-service window, and one staff-approved audio. Review Meta
   acceptance, actual recipient playback and any applicable disclosure, then delivery callbacks.
   Verify refresh/retry/restart produces no duplicate. No such test ran here.
7. Expand staff permissions/quotas only after acceptance. Continue monitoring usage,
   uncertain sends, storage and retention. No automatic replies or voice sends.

## Rollback and incidents

Preferred rollback is to disable Monara generation and sending through separately
approved gates/policy, preserving all audit/assets/send fences. Existing manual Saved
Voices and WhatsApp workflows continue. Do not remove keys/volumes, reset send status,
clear uncertain jobs or delete history as a workaround.

A code rollback to pre-Monara main must account for Alembic: **do not blindly redeploy
an older artifact that cannot recognize revision 62**. If Monara data/history exists,
retain migration 62 and its model module in a compatibility rollback build; keep the
feature disabled. Do not stamp a lower revision or downgrade populated tables. Only
when all new job/send/audit tables are verified empty, with separately approved
maintenance/release controls, may the guarded downgrade to 61 precede the old build.

On provider failure, inspect only sanitized job failure/state and usage reservations.
On uncertain sending, preserve the original intent and customer chat evidence; no
blind retry. During storage/DB incidents, stop new generation first and preserve
normal financial/Inbox services. No Shopify, payment, inventory or printer recovery
operation is part of this feature.

## Test evidence and remaining limits

**330 distinct automated cases passed; no unresolved failures or skips.** Two
SQLite-conditional Global AI cases subsequently passed explicitly on PostgreSQL.
Overlapping reruns are counted once. Existing deprecation warnings remain. Tests
include real local encoding, the pinned SDK converter, PostgreSQL quota/send
concurrency, migration/rollback/audit, media privacy, real-adapter mocked HTTP,
Global AI OFF independence, draft safety and mobile action-sheet integration.

See `monara-validation-results.json` for retrieved results and source/package hashes.
Actual rendered synthetic screenshots are in `images/monara/` at desktop and Android
viewports. External Gemini/WhatsApp calls are mocked; local FFmpeg/MP3/OGG encoding,
SDK validation and PostgreSQL transactions are real disposable tests. No physical
Android/recipient playback, live voice/style quality or external billing is claimed.

Remaining operator requirements: release/migration approval, secret installation,
current Meta documentation review, provider budget verification, retention scheduling,
approved billable voice acceptance and separately approved recipient sending.
