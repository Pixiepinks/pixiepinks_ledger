# Same-source Opus experiment — diagnosis only

## Evidence and limits

The owner supplied actual Meta-media analysis: message 1078 / Saved Voices attempt 1 still plays on Android (67,427 bytes, Opus mono 48 kHz, decoded 7.296 seconds,366 CELT packets/config 31); failed attempt 56 is 29,751 bytes with 309 hybrid and 57 CELT packets/configs 15/31. Both have 20 ms encoded packets and pass OGG validation. Hosted hashes match metadata; the failed upload hash also matches. Both ordinary-audio and voice:true delivery of the same failing bytes fail playback.

This identifies a meaningful difference, not causation. These are different original recordings. CELT and hybrid are valid Opus modes; bitrate, file size or requested encoder settings cannot establish mode. The defect was reported before 22b05f8, so that attempted fix is not assumed to explain the original regression. No permanent audio correction is included. This implementation session sent no WhatsApp messages.

The working send timestamp is27 October 2026 16:23:12 in a naive DB column (the app uses UTC by default; if UTC, 21:53:12 Sri Lanka time). Git merge7dedda2 is dated 21:47:34 Sri Lanka time, but no Railway deployment evidence links it to that send. The first-failing deployment is still unknown. Latest fetched main is eaefe9f (PR100); the last supplied runtime report showed119eccb/schema21/AI OFF. Do not assume latest Git main is the running build. See the regression investigation report for history and the missing deployment boundary. No Railway access or additional historical deployment evidence became available here.

## Same-source design

This extends the existing `/crm/bot-management/voice-diagnostics/ab` owner workflow, role/CSRF checks, owner binding, persistent private snapshots, `VoicePlaybackTest`, `VoicePlaybackVariant`, delivery webhook tracking and `BotAuditEvent`. No new database migration is required (schema20261030_21).

One explicitly owner-authorized retained source is copied once to a private immutable snapshot. Its original bytes and fingerprint are passed to all encoders. Sources must decode to0.25–30seconds, be at most16MiB, and match retained source integrity metadata where available. If only a normalized source remains, the UI explicitly labels that lossy fallback. Saved Voices originals/rows and historical messages are never re-encoded or changed.

| Variant | Requested settings | Actual evidence |
| --- | --- | --- |
| A — Historical-style | Pre-22b05f8 code profile: PyAV/libopus,mono48k,fltp resampler; bitrate/application/frame options left unspecified as before | Headers, packet TOC configs/modes/counts and durations are measured. This does not recover the historical Railway toolchain. |
| B — Current-style | Existing production normalizer unchanged: PyAV16.1.0/libopus,mono48k,flt,32kbps,voip,20ms,VBRon | Measured packet modes, not labeled hybrid/CELT from requested settings. |
| C — Independent reference | Separately invoked pinned FFmpeg/libopus,mono48k,32kbps,applicationvoip,20ms,VBRon | Runtime executable/version/hash plus measured modes. Matches B's explicit settings to compare encoding/muxing pipelines. |

All three principal messages use **ordinary audio**, with the `voice` key omitted for all. The prior A/B showed the flag alone does not resolve playback; keeping it constant removes that variable. No native-only claim is made without current documentation access. Each variant receives its own media ID and deterministic idempotency identity `encoding:<run-id>:<variant>`, backed by the existing unique(run_id,variant) constraint, a committed single run claim and each variant's conditional NOT_STARTED→UPLOADING admission. Completed/ambiguous variants cannot be retried even if a parent state is mistakenly reset. There is no TOC rewriting or manual mode forcing. Identical observed distributions are explicitly called out as not isolating a CELT/hybrid difference.

Every immutable output records source and file SHA-256/length, container/codec/rate/channels/duration, OpusHead, private OpusTags structure, page/granule/EOS evidence, packet count/mode distribution/TOC configs, encoded duration vs demux trimming, warnings, profile parameters and encoder/runtime provenance. Independent decoding produces bounded PCM, checked for success, duration agreement and errors. PCM bytes and decoder text are never logged or persisted; only hashes/counts/status are retained.

**Valid file, independently decoded, Meta accepted, WhatsApp delivered and actual Android playback are distinct outcomes.** None automatically sets the next to true.

## Optional reproducible independent toolchain

The standard application requirements and default `nixpacks.toml` remain unchanged. PyAV is neither replaced nor upgraded.

`requirements.voice-diagnostics.txt` pins the Linuxx86_64 `imageio-ffmpeg==0.6.0` wheel with SHA-256 `c7e46fcec401dd990405049d2e2f475e2b397779df2519b544b8aab515195282`. It packages an FFmpeg7.0.2-static executable; no executable is downloaded at request time. Runtime checks require the exact package version, regular executable file length79,826,272 and binary SHA-256 `e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99`, plus actual version output. Arbitrary PATH binaries and IMAGEIO_FFMPEG_EXE overrides are not used. Unsupported architecture, missing package or any integrity mismatch blocks live eligibility.

The immutable executable's own `-h encoder=libopus` output was inspected: voip/audio/lowdelay applications, frame_duration and VBR are supported; C uses explicit voip/20ms/on. Reference: https://ffmpeg.org/ffmpeg-codecs.html#libopus-1 . The binary/application version and hash are recorded; a separate upstream libopus version is not asserted when the build does not expose it. The pin is reproducible, **not a claim that this 2024 binary is the newest or security-audited FFmpeg release**. Review/update the pin through a separate tested change as appropriate. It is an optional owner diagnostic dependency, not added to the normal production path.

PyAV source inspection, structural validation, historical/current encoding and independent FFmpeg work all run in bounded children. A fresh child applies CPU15s, address-space512MiB, file-size32MiB and no-core-dump limits before exec. Parent wall timeout is25s. Input is bounded; encoded output is at most16MiB and PCM at most31s of mono48k/s16. FFmpeg runs one codec thread, accepts pipe-only protocols, and receives no application credentials. PyAV children retain only the platform LD_LIBRARY_PATH when required by Nixpacks for the existing C++ runtime; FFmpeg children do not inherit it. No shell or preexec_fn is used. The existing transport retains its normal final validation of the already-reviewed output bytes; that production helper is unchanged. These are resource/protocol restrictions, not a full OS security sandbox; use only explicitly owner-authorized sources, and keep diagnostics disabled outside controlled use.

This wheel does **not** bundle ffprobe. Runtime/UI explicitly report that limitation. The optional executable is deliberately not added to PATH: older PATH-based offline reports can still say FFmpeg unavailable; use `python -m voice_diagnostic_tools` and the new experiment runtime field to verify this pinned tool. Existing offline diagnostics can still use separately installed ffprobe when available; live eligibility here requires the pinned independent FFmpeg full decoder, not a misleading claim that PyAV is independent. If the optional tool cannot be installed safely, preparation remains OFFLINE_ONLY and cannot send.

## Deploy without enabling anything automatically

1. Review/merge this feature branch. No production encoder, MIME, payload default, Saved Voice, AI activation/learning or accounting changes are part of it. Existing A/B records continue using their original workflow; the old two-message endpoints reject three-variant experiments.
2. Deploy using the existing default build to keep the optional dependency absent, or explicitly select **`nixpacks.voice-diagnostics.toml`** for an owner diagnostic build (for Nixpacks, `--config nixpacks.voice-diagnostics.toml`; on Railway select the custom Nixpacks config path, e.g. `NIXPACKS_CONFIG_FILE=nixpacks.voice-diagnostics.toml`). The optional config extends normal dependency/setup phases, installs only the hash-pinned wheel with `--no-deps`, verifies it and records build provenance. The default config is untouched. Confirm the actual Railway build log used the selected file; do not assume an environment variable took effect.
3. For a custom existing build pipeline, append—not replace its normal dependency/provenance steps—with:

```sh
python -m pip install --no-deps --only-binary=:all: --require-hashes -r requirements.voice-diagnostics.txt
python -m voice_diagnostic_tools
```

The second command must report `available:true` and `verified:true`. It sends nothing. The optional config fails its build if verification does not pass. Do not install tools during requests or startup hooks.

4. Verify the actual running artifact SHA/file fingerprints against Railway and confirm Alembic remains20261030_21. Confirm Global AI still reads **OFF**. Existing provider credentials stay server-side; never share variable dumps.
5. Leave new **`VOICE_ENCODING_TEST_ENABLED` absent/false** by default. It must be explicitly enabled by the owner along with the existing `VOICE_PLAYBACK_TEST_ENABLED` and configured `VOICE_PLAYBACK_TEST_OWNER_NUMBER`. No recipient number is embedded in code. Merely installing the dependency or deploying never enables a test or AI.
6. After testing, disable `VOICE_ENCODING_TEST_ENABLED`. After the updated environment is deployed/restarted, it blocks preparation, sends and Meta retrieval; authorized audit review, phone-result recording and cleanup remain available. Keep normal customer voice sends under the existing operational pause until actual phone playback has been established.

## Exact owner workflow

1. Send an inbound WhatsApp message from the explicitly configured owner test phone to open the24-hour service window. Opening the CRM conversation does not open it. Global AI stays OFF.
2. ADMIN opens Voice Diagnostics → Owner A/B page. In **Same-source encoding experiment**, select one authorized short source, its revision, type the configured owner number privately and confirm source authorization. Click **Prepare three encoding variants — send nothing**.
3. Review all three profiles, actual packet counts/modes/configs, duration, hashes, exact common ordinary-audio message type and independent decode. Listen to private authenticated previews if useful. No Meta API request is made by preparation or preview. If all three do not pass, sending is blocked; after installing/repairing the toolchain prepare a fresh run. No hidden promotion/retry exists.
4. Confirm the warning: configured owner-controlled destination, owner authorization, open24-hour window and **exactly THREE messages** (A→B→C if successful). Click **Run encoding test — Send Three Messages** within15minutes. Every snapshot and independent decode is rechecked before the first upload. Expiry, config and service window are rechecked at each upload/send boundary. All sends use the same server-configured business phone and recipient.
5. If upload/send fails or is ambiguous, the sequence stops. Remaining variants stay NOT_STARTED. Do not create another run to retry until the existing media/message evidence is investigated. An external API request already started cannot be recalled; durable SENDING/RUNNING states deliberately prevent automatic resends after crashes or lost acknowledgements. This is not an atomic three-message transaction.
6. On Android, check each exact message. Record **Plays correctly / Cannot play / Cannot download / Cannot share / Not tested**, with platform. Stored manual results bind the exact immutable fingerprint, variant, media ID and WAMID. Delivery200/delivered/waveform never fills playback automatically. A cannot-download/share result is not automatically treated as proof that playback also fails. Refresh for webhook status.
7. Optionally explicitly confirm **Inspect existing variant media — send nothing** to retrieve those existing Meta objects and compare hashes. No automatic retrieval or sends occur. Unlike old A/B, each variant's expected hash is its own output hash.
8. Keep the owner chat quiet during the test. The intended send order is A→B→C, but provider delivery ordering cannot be guaranteed. If the owner cannot reliably identify which exact audio corresponds to a variant, record Not tested rather than guessing from the waveform. No extra label messages are sent.
9. Once done or abandoned, click **Close test and delete experimental audio**. Only new source/variant snapshots are deleted; original Saved Voices, audit, hashes, Meta IDs and manual results remain. Preview/send is then unavailable for that run. A storage deletion failure is audited, keeps the run closed to sends and leaves a retry-cleanup control after permissions are repaired. Transient worker audio lives only in pipes; offline temporary directories from the existing tools are removed by their scoped context.
10. A RUNNING/interrupted sequence cannot be closed while a worker may still be using it. Investigate its network/worker state before privileged recovery; do not blindly mark it retryable. Snapshots awaiting that investigation are retained privately, not publicly served.

## Interpret outcomes without overclaiming

* A plays/B fails: strong profile-compatibility evidence, but bitrate, resampler, application and pipeline differences also exist. CELT/hybrid alone is not isolated.
* Both PyAV outputs fail/C plays: investigate PyAV configuration, muxing and finalization.
* All fail: investigate common upload/MIME/provider/client behavior; do not deploy the old encoder as a presumed fix.
* A and B play: investigate recording-specific and historical conditions. One successful test does not establish that the regression is fixed.
* Actual modes fail to differ: report that distribution. No CELT-vs-hybrid causal conclusion is possible from that source/profile pairing.

AAC/M4A live control stays **disabled**. Official Meta messages/media documentation fetches again returned proxy403 during this task. Thus current format-specific normal-audio support could not be independently verified; no AAC endpoint, voice-note conversion or feature switch can silently enable it. The existing offline AAC analysis remains available. If a future separately verified ordinary-audio AAC control plays while Opus fails, investigate format/provider handling, not presumed account restriction.

Official references for later verification: https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media and https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages . No Meta account restriction is demonstrated by the available evidence, and no number/account registration settings were changed.

## Audit, tests and rollback

Successful preparation, three-send authorization, sequence result, private previews, explicit media retrieval, manual playback changes and cleanup write the existing append-only BotAuditEvent with administrator ID, run ID and timestamp. Per-variant durable status/trace contains its idempotency identity, source/output fingerprints, safe multipart/send evidence, media/WAMID and webhook status. Source filenames, audio contents, decoder text, phone numbers, credentials and signed URLs are not added to logs. Failed transport states are retained; authorization failures send nothing.

Existing run/variant tables and webhook paths are reused. No schema migration. Rollback: disable the new flag first, close non-running experimental snapshots after saving evidence, then redeploy the prior verified artifact/config. Do not delete originals or old A/B records. Leave interrupted sends fenced until investigated. Optional wheel removal needs a normal redeploy; nothing uninstalls or changes PyAV.

Files: `voice_encoding_experiment.py`, `voice_diagnostic_tools.py`, `requirements.voice-diagnostics.txt`, optional `nixpacks.voice-diagnostics.toml`, `templates/voice_encoding_test.html`, and tests. Integration: existing `voice_playback_routes.py`, `voice_playback_test.py` (cross-workflow guards only), `templates/voice_playback_test.html`, `voice_compatibility.py` (measured mode counts) and `build_info.py` provenance. Default `audio_service.py`, `whatsapp_service.py`, requirements, Nixpacks config, settings, models, AI controls and original records are unchanged.

## Verification results

* **34 new tests passed**, covering real pinned independent encoding/full PCM decoding, bounded PyAV children, >30s rejection, immutable same-source fingerprints/measured mode counts, exact multipart file bytes/MIME, three equal ordinary-audio payload types, independent media/WAMID references, no automatic sends, role/CSRF/owner/count/window/expiry gates, later-file corruption blocking every send, mid-flight expiry, partial/unknown failures, per-variant replay protection even after erroneous parent reset, concurrent PostgreSQL workers, existing-A/B bypass rejection, manual phone results, webhook delivery, explicit Meta retrieval, private previews, original-file preservation and cleanup success/failure/retry. Chromium exercises preparation, required three-send confirmation and manual results with mocked Meta calls.
* Combined relevant suite: **232 passed, 1 deselected**, with isolated PostgreSQL and mocked Meta calls. Command: `python -m pytest -q tests/test_voice_encoding_experiment.py tests/test_voice_regression_diagnostics.py tests/test_voice_playback_test.py tests/test_voice_production_trace.py tests/test_voice_audio_compatibility.py tests/test_audio_service.py tests/test_saved_voices.py tests/test_whatsapp.py tests/test_global_ai_control.py tests/test_learning_migration.py tests/test_crm_whatsapp_inbox.py -k 'not test_inbox_requires_authentication_and_renders_for_staff'`. Final authorization-reason/UI-label changes were then verified with **68 passed** across the new and existing owner-workflow suites.
* Known baseline: the deselected Inbox test expects obsolete `whatsapp_inbox.js?v=inbox-colombo-time-1`, whereas main's existing template uses `saved-voices-1`. It is independently run/reported as an existing failure, not hidden by changing unrelated production code.
* Optional hash-mode wheel installation succeeds; the actual executable/version/hash verifies and independently decodes the generated profiles. Both Nixpacks TOML files parse. Standard production-only Python3.12 dependencies still pass `pip check` and compilation with the optional package absent; bounded PyAV workers still function there.
* Actual local Uvicorn startup/restart checks pass for SQLite and PostgreSQL, including two web instances sharing Global AI OFF, retained normalized audio, and an explicit missing-tool preparation test. With synthetic owner/provider configuration and test flags enabled but no optional executable, the run is OFFLINE_ONLY with no send control and **zero outbound messages**. No real provider credentials/customer data were used.
* No default production encoder/helper, default dependency/build config, models/schema, AI/learning settings or originals changed. No live experiment was executed. Android playback results for these same-source variants are still required before any permanent correction can be justified.
