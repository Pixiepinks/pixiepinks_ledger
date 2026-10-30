# Owner-controlled voice playback comparison

## Evidence and investigation

The owner supplied these production observations, not independently retrieved by this implementation session:

* Saved Voices attempt 56, recording 10, media 1460902032708432: mono 48 kHz OGG/Opus, 7.296 seconds, 29,751 bytes; upload/send HTTP 200, delivered, Meta-downloaded bytes matching the upload; phone playback failed.
* Browser voice attempt 1422, media 1136784228745585: mono 48 kHz OGG/Opus, about 4.86 seconds, 19,555 bytes; same acceptance, delivery, byte agreement and playback failure.
* Running commit reported as `6bb39f75a69fecb76a786ca8d17cbb3331e0aaea`, schema 20261030_20, PyAV 16.1.0, Graph v26.0, normalization version 1, Global AI disabled.

The latest fetched main was 6bb39f7 and contains the earlier byte-tracing diagnostics. Both routes converge on `audio_service.normalize_voice_audio`, `whatsapp_service.upload_whatsapp_audio` and `send_whatsapp_audio`. The encoder uses real libopus in OGG, mono 48 kHz, 32 kbps VBR, voip application and 20 ms frames. It flushes both the resampler and encoder and closes the muxer. Existing validation checks OGG CRC/serial/sequence/BOS/EOS, OpusHead and independent full decode. Upload uses actual validated bytes, audio/ogg in multipart, the business phone ID `/media` endpoint, and the returned ID in `/messages`. Both current production paths request `voice:true`.

Byte agreement and delivered status narrow the investigation but **do not prove client compatibility or identify a root cause**. The shared voice flag, shared encoding/muxing characteristics and recipient client remain hypotheses. No original production audio was downloaded into this workspace. No permanent encoding or payload change is justified yet.

`voice_compatibility.inspect` adds offline evidence for Opus identification version, channels, input-rate hint, pre-skip, gain, mapping family, OpusTags presence, final granule, monotonic granules, packet durations, page count and EOS. It does not export comments/vendor text or claim WhatsApp certification. Existing production normalization and validation behavior is unchanged.

### Official references and access limitation

References for review in a browser with Meta documentation access:

* https://developers.facebook.com/docs/whatsapp/cloud-api/messages/audio-messages
* https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages#audio-object
* https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media
* https://developers.facebook.com/docs/whatsapp/cloud-api/support/supported-media-types
* https://developers.facebook.com/docs/graph-api/changelog/
* OGG Opus framing reference: https://www.rfc-editor.org/rfc/rfc7845

On 30 October 2026 the official Meta audio page could not be retrieved from this execution environment: the proxy returned HTTP 403 and direct DNS resolution failed. Therefore **current official `audio.voice` support, version-specific voice requirements and any additional encoding restrictions have not been independently verified**. Do not treat guessed additional restrictions as documented requirements. The tool intentionally compares the requested payloads experimentally; it is disabled by default. Review those references before enabling it. Existing Graph version and request construction are preserved. Ordinary audio omits `voice` entirely; B includes `voice:true`. It uses separate media uploads of identical bytes, avoiding an unverified assumption about reuse across message modes.

## Deployment

1. Review/merge the feature branch. Deploy the actual Railway web service; verify its runtime artifact SHA on the existing diagnostics page against the Railway deployment, not just GitHub.
2. Apply additive Alembic revision **20261030_21** using the existing migration/startup procedure (`python -m alembic upgrade head` if running explicitly). Confirm `python -m alembic current`. Migration adds only `voice_playback_tests` and `voice_playback_variants`; no data backfill, encoding changes, AI settings changes or historical media updates.
3. Existing PyAV/FFmpeg dependencies and `/data` persistent Saved Voices directory remain required; no new dependencies or Railway build configuration changes.
4. Default: leave `VOICE_PLAYBACK_TEST_ENABLED` absent/false. The tool is visible to ADMIN users only and cannot prepare or send.
5. After reviewing official documentation, the owner can provision **one** explicitly verified owner-controlled international number (digits only, no `+`) as `VOICE_PLAYBACK_TEST_OWNER_NUMBER` in Railway. Set `VOICE_PLAYBACK_TEST_ENABLED=true` only for the controlled test. No number is embedded in code or automatically selected from customers. Never paste variable dumps, tokens or the number into diagnostics.
6. Global AI must remain OFF. This release neither reads nor changes learning configuration or AI activation as part of a test. These test messages use the existing manual staff transport, not an AI job.
7. Owner must send an inbound WhatsApp message to the configured business phone first. A genuine customer-service window under 24 hours is checked at prepare, run, and before each upload/send. Opening a conversation does not open this window.

Do not execute a test as part of deployment or an automated health check. To disable the tool, remove/set false the feature variable; production audio routes remain unchanged. Old snapshots/records remain intact. An external request already in progress cannot be recalled by toggling a deployment variable.

## Exact administrator workflow

1. Log in as ADMIN. Open Bot Management → Settings → Voice diagnostics → **Voice Playback Compatibility Test — Owner Only** (`/crm/bot-management/voice-diagnostics/ab`). GET sends nothing.
2. Select a READY/current Saved Voice, enter the revision shown, type the configured owner number privately, and confirm that you verified owner control and authorization. Click **Prepare review — send nothing**.
3. Review the immutable preview, recording revision and A/B payload differences. Preparation copies the existing normalized bytes once to a private, integrity-checked snapshot; neither variant re-encodes it. Originals and the Saved Voices library stay untouched. The number is never echoed in HTML/evidence.
4. Confirm the warning and click **Run A/B Test — Send Two Messages** within 15 minutes. This is the only action that sends. A is ordinary audio with the voice key omitted. B uses `voice:true`. Two messages are sent if both stages succeed; a partial or ambiguous failure stops the test without retry.
5. Refresh for send/delivery status. Actual delivery does not mark playback successful. On the owner phone play both recordings and select each actual result, client platform and optional safe error category. Save results. Do not enter customer details or secrets.
6. Optionally confirm **Inspect Meta media** to retrieve the existing two media objects and compare their hashes to the snapshot. This performs read-only Meta retrieval, no sends.
7. Review the classification and expanded evidence. Provide run ID, statuses, stream evidence, and A/B **actual playback** results and platform. Do not provide phone numbers or credentials.
8. Disable the feature after the test. Keep customer voice sends paused until successful phone playback has been established and a targeted fix (if needed) verified.

## Security, concurrency and failure semantics

* ADMIN only, including preview, results and Meta retrieval. Each run belongs to its preparing administrator. A different administrator cannot execute or inspect it through this tool.
* Server-side destination comes exclusively from the explicit owner deployment configuration; typed verification and an attestation are required. This is deployment-owner attestation, not SMS OTP proof of possession. No arbitrary recipient/conversation selector exists. Configuration binds recipient, business phone ID and Graph version to the review via a non-reversible SHA-256 fingerprint, never logging the number itself.
* Session CSRF checks on every POST. Preparation never sends. The final two-message confirmation is separate.
* A conditional PostgreSQL/SQLite update REVIEW→RUNNING commits before external I/O. Concurrent workers/double clicks can claim a run once. Run ID is the idempotency identity. Variant states UPLOADING/SENDING commit before external calls. Unknown, interrupted or failed attempts are never replayed or resumed automatically. A new run always requires a new review/confirmation.
* Crashes can produce fewer than two messages, including an ambiguous external send. Inspect Meta/webhook evidence before making a new test. Neither this tool nor Cloud API provides an atomic two-message transaction or guaranteed exactly-once external delivery after a network failure. Never promise both sends in a failure case.
* Byte fingerprints are captured in the immutable snapshot, validation and actual multipart file stage. Each variant records its own media ID, safe payload, upload/send HTTP status/codes, WAMID and delivery result. Webhook processing is additive and stores only numeric errors. An extremely fast webhook before the returned WAMID is persisted can be missed; verify Meta/provider evidence if delivery remains unknown. No delivery or HTTP status proves playback.
* Read-only Meta retrieval uses the existing bounded HTTPS trusted-host reader. No credentials, signed URL, source name, transcript or customer phone enters the evidence. Error descriptions are selected categories rather than unrestricted text to reduce accidental PII disclosure.
* Immutable snapshots use the existing persistent private storage mechanism; one replica or shared storage is still necessary. A second worker without the volume fails before sending. Snapshots intentionally remain for investigation; cleanup/retention is manual and must not delete originals. A failed database commit during preparation can leave an orphan snapshot, never a send.
* No existing message/history records are edited by this tool. Diagnostic tests live in separate audit tables, not customer conversations. Existing receipt handling, CRM, learning and accounting continue unchanged.

## Evidence required for a correction

A plays/B fails: investigate voice-note-specific stream/metadata and semantics. Both fail: shared media/client compatibility. Both play: compare controlled-path evidence with production routes. B plays/A fails: ordinary attachment requirements. These are investigation directions, not proof.

Require A/B phone results, platform, immutable upload/download hash agreement, full stream evidence, exact redacted payloads, current runtime and version-specific documentation before selecting a correction. If platform-specific, repeat an explicitly authorized new run on another owner-controlled client. No speculative permanent fix is included and no live sends were executed during implementation.

## Files and validation

New implementation: `voice_playback_test.py` (claims/sends/results), `voice_playback_routes.py` (admin/CSRF routes), `voice_compatibility.py` (offline stream evidence), `templates/voice_playback_test.html`, migration `20261030_21_voice_playback_test.py`, and `tests/test_voice_playback_test.py`. Integration changes: `main.py` (router and additive delivery status updates), `models.py` (two diagnostic audit tables), `build_info.py` (provenance coverage), and `templates/voice_diagnostics.html` (entry link). Existing migration-head assertions were updated in `test_global_ai_control.py`, `test_learning_migration.py`, `test_saved_voices.py` and `test_voice_production_trace.py`; the Saved Voices migration fixture excludes the new dependent diagnostic tables until their migration creates them.

Validation on 30 October 2026:

* New A/B suite: **34 passed**, including real HTTPX multipart construction, mocked Meta calls, SQLite and PostgreSQL concurrent requests, and Chromium owner review/confirmation/results.
* Relevant combined suite (new tests, audio, Saved Voices, existing diagnostics, WhatsApp/webhooks, Inbox, Global AI and learning migrations): **181 passed, 1 deselected**. Command: `python -m pytest -q tests/test_voice_playback_test.py tests/test_voice_production_trace.py tests/test_voice_audio_compatibility.py tests/test_audio_service.py tests/test_saved_voices.py tests/test_whatsapp.py tests/test_global_ai_control.py tests/test_learning_migration.py tests/test_crm_whatsapp_inbox.py -k 'not test_inbox_requires_authentication_and_renders_for_staff'`, with isolated `TEST_POSTGRESQL_URL` and `AUTO_PRODUCTS_TEST_DATABASE_URL` pointing to the synthetic local database.
* The deselected Inbox test was first run and failed on an existing stale cache-string assertion: it expects `whatsapp_inbox.js?v=inbox-colombo-time-1`, while main's template already uses `v=saved-voices-1`. Both the assertion and template mismatch were verified in fetched main. No unrelated production/UI changes were made to hide it.
* Production-only Python 3.12 environment: `pip check` and compilation passed. Actual Uvicorn startup with two web instances passed on both SQLite and PostgreSQL, including additive migration, default-disabled tool, AAC upload/normalized preview, shared Global AI OFF and restart persistence. Provider credentials were removed; no external messages occurred.
* Existing `audio_service.py`, `whatsapp_service.py`, `global_ai_control.py`, settings and Railway build configuration are unchanged. No real production or test phone playback was performed. Automated success is not playback acceptance.
