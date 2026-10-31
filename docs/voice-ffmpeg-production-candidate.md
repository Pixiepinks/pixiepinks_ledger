# FFmpeg production encoder candidate — 31 October 2026

## Status and evidence boundary

**Candidate ready for controlled owner verification; production issue is not declared resolved.** `VOICE_FFMPEG_ENCODER_ENABLED` defaults to false and was not enabled in production. No real WhatsApp messages were sent, no deployment/merge was performed, and Global AI settings were not changed.

The initial task cited experiment `f88bbb27284c460f94bcf0f70254758d`. The owner clarified that this earlier run was skipped. The completed report is **`f3041804024f444a925f42a6125d255b`**, created31Oct03:40:44 UTC (09:10:44 Sri Lanka), authorized03:41:01 UTC (09:11:01 Sri Lanka), source recording4/revision2. All variants declare the same retained-original source SHA `1b72d234503ed3a70a22cdf46b44787d53d0a6ac843f1ab561849bb75932866d`,92,925bytes.

| Variant | Uploaded SHA-256 | Bytes | Media ID | Mode / phone result |
| --- | --- | ---: | --- | --- |
| A historical PyAV | `c7858e2d910ea4961331bcae3c87fc9a5485072e4a5d5a93ba941afa2339447c` | 91,914 | 1125249896588047 | 529 CELT packets/config31; cannot play |
| B current PyAV | `d3d4672ab0a333b8bb33f560b7c5d58c82d45364909331c9d192f0c3f7136d9b` | 42,123 | 1782592343868747 | 529 hybrid/config15; cannot play |
| C independent FFmpeg | `33652e248d905588838363e6ea8e65e2d668232025ecc2853ea62a2f8ee2e629` | 42,013 | 2564725477357145 | 529 hybrid/config15; plays |

These values come from owner-supplied production JSON/screenshots, not direct Railway/API access from this workspace. In each trace the immutable file, upload validation and actual multipart SHA/length agree; upload and message send returned HTTP200 without numeric errors. Media IDs match each payload and send-result WAMIDs match the stored WAMIDs (distinct across variants). Opaque WAMIDs remain in the private authenticated evidence rather than this repository report. All uploaded MIME values are `audio/ogg`, all Graph versions arev26.0, all recorded upload/send business-phone hashes agree, and all audio payloads omit `voice`. No unexpected **recorded** business routing differences are apparent. Raw recipients are deliberately absent from traces; owner binding/code and owner-phone observation support recipient consistency rather than an independent raw `to` audit.

The export initially had `playback:null`, results_by/results_at null, delivery A=delivered/B=sent/C=null. The later screenshot shows saved phone fields A=cannot play/B=cannot play/C=plays, with delivery A/B=delivered/C=sent. Do not silently mark C delivered from its playback result. Updated raw playback/audit JSON and matching append-only BotAuditEvent entries were not supplied. Embedded actions confirm preparation, authorization of three and sequence completion. Direct production database/control-plane access and exact raw audio were unavailable here. Meta-downloaded fingerprints for **this completed run** were not included; earlier experiments' download matches are not substituted.

Runtime in the supplied trace reports artifact/platform `720191ac7d261529180f1ae507ab8281a5d7827c` and matching files. Git SHA-256 recomputation for audio_service.py, voice_diagnostic_tools.py and voice_encoding_experiment.py at720191a matches the supplied runtime hashes. This corroborates the owner runtime report but does not replace independent Railway control-plane verification. Historical working deployment for message1078/attempt1 and the first-failing deployment remain unidentified; Git times are not used as deployment proof.

## PyAV versus independent output

Both B and C have OpusHead v1, mono48k, pre-skip312, gain0, mapping0,531 total packets (529audio),13pages,20ms encoded packets, EOS, no reported structural warnings and independently decoded10.56s/1,013,760PCMbytes. Therefore hybrid alone is not an unsupported format explanation: B and C are both entirely hybrid and have opposite owner playback results.

The vendor hashes match known strings: PyAV's `d207…` is SHA-256 of `Lavf62.3.100`; C's `57eb…` is `Lavf61.1.100`. Source parameters identify PyAV16.1.0/libavcodec62.11.100/libavformat62.3.100 versus the exact FFmpeg7.0.2-static executable whose binary SHA is `e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99`. Both B/C request32kbps,voip,20ms,VBRon,mono48k. Separate upstream libopus build versions are not exposed; the executable hash fixes the independent build reproducibly.

**Every listed audio-page granule in C is B+1**, not only the final507184/507185 value. The difference is1/48000second (20.833microseconds). PyAV A and B have the same listed granules despite differing modes. Independent PCM lengths are identical but PCM SHA-256 differs between B and C. Thus output differences include decoded content/codec behavior, tag/muxer builds and timestamps; the one-sample offset is not isolated as causal. Decoder duration rounding cannot resolve it.

`voice_packet_analysis.py` now captures bounded page flags/sequence/serial/CRC validity/lacing, completed packet boundaries, per-packet sizes/SHA/TOC/config/sample counts, cumulative samples and granule offsets, without audio or tag text. Comparison distinguishes codec packet identity from metadata/container differences. The read-only exporter/UI can retrieve these exact existing media IDs and optionally remux with the pinned executable, then verify codec-packet fingerprints stayed identical. It never uploads/sends the old files or changes old phone results.

Offline synthetic44.1k AAC/M4A experiments found matching OpusHead/coded durations and equal granules across the two encoders, but different packet payloads from the first audio packet and different tags/lacing. A pinned remux preserved all codec packet bytes while changing tags. This is synthetic evidence, **not reproduction of the production failure**. The actual production packet/lacing comparison still requires running the read-only tool against the existing owner media.

Useful remaining isolations: (1) same packet bytes remuxed through one muxer, (2) zero-based integer sample-clock timestamps while retaining the same decoded samples/settings, (3) identical canonical PCM supplied to both encoder builds, separating AAC decoding/resampling from encoding, and (4) isolated one-sample container timing probes with independent validation. None should become a permanent fix without new owner-authorized phone evidence. No packet TOC rewriting or claim that CELT is universally supported/hybrid unsupported is made.

## Exact candidate code path

`audio_service.normalize_voice_audio(raw, content_type='') -> (bytes, duration_float)` keeps the public interface. OFF calls the unchanged `_normalize_pyav` implementation. ON calls `voice_ffmpeg_encoder.normalize`; errors raise safe VoiceAudioError with **no PyAV encoding fallback**. PyAV is still used in bounded children for input inspection and structural validation, not as a fallback encoder.

The same pinned package/executable as successful C is required: imageio-ffmpeg0.6.0 Linuxx86_64 wheel, hash-pinned in the existing optional requirements, executable length79,826,272/SHA and actual version verified. No runtime binary download or global PyAV upgrade. Output remains genuine OGG/Opus,mono48k,32kbps,voip,20ms,VBRon,one codec thread; normal WhatsApp MIME remainsaudio/ogg and existing sending helpers are unchanged.

Two documented compatibility changes from C's diagnostic command:

1. **Duration guard31→601seconds**, because the existing public interface supports recordings up to10minutes. Source actual decoded duration is checked against600seconds first, output and independent decode must agree within50ms, and longer input is rejected rather than silently truncated. Short-input codec packet identity against the successful-C command is tested.
2. **Sealed seekable anonymous descriptor instead of non-seekable stdin** for production encoding. A valid synthetic M4A with trailing `moov` metadata failed the original pipe path. The pinned executable's actual `fd` protocol and `-fd` option were verified locally. Input is bounded to16MiB, placed in a sealed Linux memfd (write/grow/shrink/seal checked), and read with `-protocol_whitelist fd,pipe -fd <owned-fd> -i fd:`. No disk source/cache, broad file/network protocol or customer filename is used. Codec settings are unchanged. This input issue does not explain already delivered unplayable recordings.

The effective encoding arguments after input are:

```text
-map 0:a:0 -vn -sn -dn -t 601 -threads 1 -ac 1 -ar 48000
-c:a libopus -b:a 32k -application voip -frame_duration 20 -vbr on -f ogg pipe:1
```

Production codec operations run in fresh children with512MiB address-space,90CPU-second,120wall-second-per-stage,32MiB file-size and no-core-dump bounds. Input/output compressed size is at most16MiB; independent PCM output is bounded to601seconds mono48k/s16 (~57.7MB), never persisted/logged. At most two candidate conversions run per web process; saturation gives an explicit retryable staff error, not fallback. These are resource/protocol restrictions, not an OS security sandbox. Four bounded stages can take up to their individual timeouts; monitor real service latency/memory before rollout. The optional tool remains an older reproducible diagnostic build, not a claim of latest upstream security status.

Safe structured events `ffmpeg_normalization` and `audio_encoder_metric` record backend, input/output hashes/lengths, actual formats/durations, executable provenance, outcome/reason and elapsed milliseconds. No audio, transcript, phone, token, signed URL or decoder text is logged. Process-local calls/success/error/latency counters appear in Voice Diagnostics alongside the effective production flag; they include owner/self-check calls and are not global business statistics.

`NORMALIZATION_VERSION` remains1: activating the flag does not invalidate/re-encode READY historical rows. Old files and originals remain untouched. When FFmpeg is enabled, legacy/failed recordings requiring preparation are rejected with a clear upload-new-recording instruction rather than rewritten. Newly uploaded Saved Voices and new browser recordings use the selected public encoder. Already-stored bad recordings do not become fixed simply by enabling this flag.

## Phase1 existing evidence — no resends

ADMIN may open the completed f304… experiment and use **Read existing experiment evidence**. Confirm read-only authorization; optional separate checkboxes request existing Meta GET/bitstream inspection and local remux. The original three messages are never resent. Full stored media/WAMID references, upload/send traces, phone results, matching append-only audit entries and B/C comparison are returned in the authenticated no-cache page. Audits record the read itself without changing the historical run's results.

Privileged Railway/operator shell alternative (existing credentials remain server-side):

```sh
python -m voice_experiment_evidence --run-id f3041804024f444a925f42a6125d255b --owner-authorized
python -m voice_experiment_evidence --run-id f3041804024f444a925f42a6125d255b --owner-authorized --retrieve-meta --offline-remux
```

These perform database reads and optional bounded trusted-host Meta GETs only. No public debug endpoint or send capability is provided by the CLI. Keep the resulting report private because it includes opaque message IDs. No media bytes, comments or signed URLs are printed. Missing retrieval/phone/audit evidence stays unknown.

## Fresh owner verification — global encoder flag stays OFF

A separate **`VOICE_FFMPEG_OWNER_TEST_ENABLED`** defaultsfalse and requires the existing owner-test flag and explicitly configured owner number. It does not enable production encoding. Existing run/variant/audit tables are reused, no migration.

1. Deploy reviewed candidate code with `VOICE_FFMPEG_ENCODER_ENABLED=false`, Global AI still OFF, the existing optional pinned-tool build selected and its runtime verified. Verify each serving web/worker artifact against Railway; do not rely on a push or mixed old processes.
2. Enable only the owner verification flag/configuration for the controlled test. Confirm the existing f304… C result. If its old raw playback field is still missing, preparation records an explicit owner attestation in the **new** run and marks the historical gap; it never fills the old record automatically. A contradictory stored C failure must be reviewed manually.
3. Upload a **new/different short owner recording** in Saved Voices after that reference experiment. Keep the original on the owner's device too. Do not send its current library attachment from Inbox. With the production flag OFF that library normalization remains PyAV; its immutable retained original is used to exercise the candidate. The candidate requires a newer library creation time, different recording ID and different source fingerprint from the reference.
4. Open Owner Diagnostics → **Verify production FFmpeg candidate**, enter the completed reference ID, select that new source/revision, privately verify the configured owner number and explicitly attest source consent/reference playback. Choose **native voice note** to test the actual Saved Voices/browser production delivery flag. An ordinary-audio control can be a separate explicitly authorized one-message run.
5. Click **Prepare production candidate — send nothing**. The actual public `normalize_voice_audio` entry point executes under a private ContextVar forcing only this authorized request through the same FFmpeg branch used by the global ON flag. The scope resets even on exceptions and does not affect concurrent requests or mutate environment/config. It generates new bytes; the previous experimental file is not reused. Evidence requires the actual FFmpeg normalization trace and records the global flag's unchanged value.
6. Review the fresh source/output fingerprints, measured stream, trace/backend and exact native/ordinary payload. After a new owner inbound message opens the24-hour window, confirm **exactly ONE candidate message** and click **Send One Candidate Message** within15minutes. Server rechecks role, source/output integrity, decoder, owner/business binding, window, confirmation and expiry. A durable run/variant claim prevents duplicates; UNKNOWN/failed sends never retry. Global AI stays OFF.
7. Check actual Android playback and record its result/platform against exact hash/media/WAMID. Optional explicit existing-media inspection compares the hosted file hash without sending again. Ordinary success alone does not validate native voice-note behavior. Do not declare success from HTTP200, delivery or waveform.
8. Close the test and delete only candidate snapshots after saving evidence. Source library originals and audit remain. No customer-facing tests or unattended sends occur. Browser/live-one-message tests were not executed during implementation.

## Staged rollout and immediate rollback

**This task stops before enabling the production flag.** Suggested later stages require explicit owner approval:

* Stage0: code/tool deployment, flagOFF, migrations unchanged, normal behavior unchanged. Verify tool hash/version, effective flag, process/artifact identity and Global AI OFF on all instances. Resolve any missing evidence using read-only GETs.
* Stage1: the fresh owner-only ordinary and **native** tests above, under private request-local candidate scope. Test more than one new sample/input format (at least owner M4A and a browser recording), on actual Android; include iPhone if relevant. Do not test on customers. Save exact hashes/results. Native playback must be confirmed before the native production workflow is considered verified.
* Stage2: isolated staging flagON tests of new Saved Voices upload and browser recording with mocked Meta, malformed input/timeouts/missing executable, historical preservation and concurrency. Monitor memory/latency limits and safe error/metric events. No silent fallback is acceptable.
* Stage3: only after separately authorized approval, update the global flag and restart/deploy every serving instance. Confirm no old worker is still encoding via PyAV. First exercise the actual globally enabled new-upload/browser paths with the owner/internal number, not customers. Publish/select new verified library entries explicitly; do not migrate old media. Resume ordinary staff use only after these checks, with metrics and manual feedback monitored. This implementation did not perform these steps.

Immediate rollback procedure: pause staff voice sends, set `VOICE_FFMPEG_ENCODER_ENABLED=false` and disable `VOICE_FFMPEG_OWNER_TEST_ENABLED`, restart/redeploy all web/worker instances, verify effective flags/artifacts and synthetic checks on each. Environment edits do not stop already-running old processes or recall requests already submitted to Meta. In-flight UNKNOWN sends must be investigated, never replayed. OFF restores the previous PyAV path for **new** encodes; it does not mean that previously failing playback is repaired, so keep voice sends paused if needed. Text, product cards, attachments and normal CRM work remain available. Existing verified FFmpeg files/originals remain intact, no data rollback/re-encoding/deletion is required, and Global AI remains OFF throughout.

## Changes and known limits

Changed audio interface implementation only through flag dispatch (`audio_service.py`), added `voice_ffmpeg_encoder.py`, extended the existing pinned worker (`voice_diagnostic_tools.py`) for full interface duration/seekable input, and added an FFmpeg-active legacy preparation guard (`saved_voice_service.py`). Existing profile comparisons explicitly retain the PyAV implementation rather than following the new production flag.

Owner tools: `voice_ffmpeg_owner_test.py`, authenticated candidate template/routes, read-only `voice_experiment_evidence.py` and `voice_packet_analysis.py`, runtime metrics/provenance integration and tests. No model/schema/default dependency/default Nixpacks/WhatsApp payload helper/global AI/learning/Shopify/order/accounting changes. No migrations; optional tool dependency/build requirements are the existing hash-pinned ones from the successful-C experiment.

Current Meta docs could not be accessed in preceding tasks (proxy403); no speculative MIME, account restriction or voice flag change is introduced. AAC live controls remain disabled. Exact bitstream causation, updated private audit/result export and raw production packet/lacing analysis remain pending operator access; source/object SHA matches alone do not prove a field causes WhatsApp playback failure. The success criterion here is a candidate ready for controlled verification, not production resolution.

## Verification results

* **27 new candidate tests passed**. Includes flagOFF/ON dispatch, real pinned FFmpeg output, codec-packet identity for short inputs versus C, long31s input beyond the diagnostic cap, >600s rejection, corrupt/missing tool and encoding/decode failures, explicit120s timeout/no fallback, context isolation across threads/exceptions, actual public-path evidence, native versus ordinary payloads, fresh-source/time/fingerprint admission, owner role/CSRF/confirmation/count/expiry, concurrent one-send claim, UNKNOWN no-retry, hosted-media confirmation, cleanup preservation, historical no-re-encoding, real Saved Voices upload/browser flagON integration and Chromium one-message confirmation/manual results. All external Meta calls were mocked.
* The M4A regression fixture deliberately has trailing metadata and exceeds the initial seek buffer. The old non-seekable command emits demux errors (it may still exit0); its strict wrapper rejects it. The new sealed descriptor path encodes the complete expected duration and independently decodes successfully. This is a separate input-compatibility regression test, not an assertion about the original phone defect.
* Final relevant combined suite: **259 passed, 1 deselected**, isolated PostgreSQL and mocked Meta: `python -m pytest -q tests/test_voice_ffmpeg_candidate.py tests/test_voice_encoding_experiment.py tests/test_voice_regression_diagnostics.py tests/test_voice_playback_test.py tests/test_voice_production_trace.py tests/test_voice_audio_compatibility.py tests/test_audio_service.py tests/test_saved_voices.py tests/test_whatsapp.py tests/test_global_ai_control.py tests/test_learning_migration.py tests/test_crm_whatsapp_inbox.py -k 'not test_inbox_requires_authentication_and_renders_for_staff'`.
* Known baseline assertion rerun separately: **1 failed**. The pre-existing Inbox rendering test expects cache tag `inbox-colombo-time-1`, while main's existing Inbox template uses `saved-voices-1`. It predates this change; no unrelated Inbox code was modified to hide it.
* Production-only Python3.12 dependency check and compilation passed. Real two-instance SQLite/PostgreSQL startup/restart checks passed with the new encoder OFF, provider credentials removed, normalized recordings persistent and Global AI OFF retained. The missing optional tool does not break flagOFF startup.
* Real production raw media was not downloaded in this workspace and the candidate has not been played on the owner's phone. Those remain controlled owner/operator verification steps. No production flags, AI settings or remote account settings were changed, and no live WhatsApp messages were sent.
