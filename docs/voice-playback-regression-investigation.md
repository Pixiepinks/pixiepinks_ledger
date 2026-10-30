# WhatsApp voice playback: regression investigation, 30 October 2026

## Finding and confidence

**Root cause remains unconfirmed. No permanent audio fix is included.** Owner-reported A/B results say both ordinary audio and `voice:true` fail on Android using the same bytes. This is strong evidence that toggling the voice flag alone is insufficient. It does not establish an encoder defect or an account restriction.

The task's other owner-reported observations remain important: Saved Voices attempt 56 and browser voice attempt 1422 were accepted/delivered, Meta-downloaded bytes matched the upload, and the phone could neither play nor download/share them. Other users' native WhatsApp recordings play on that phone. These are not independently observed production results from this workspace. We have no Railway shell, production database, Meta token or authorized production audio file here. No real messages were sent during this investigation.

## Last working / first failing version

**An owner-confirmed Android-working reference is now established; its deployment commit and the first failing deployment are not.** Saved Voices attempt **1**, voice **1**, revision **1**, local message **1078**, media **1890167155698683**, status SENT was created `2026-10-27 16:23:12` (database timestamp, timezone unverified; if UTC, 21:53:12 Sri Lanka time). The owner explicitly confirmed that it **still plays in the recipient's Android WhatsApp**, not just the Inbox.

The owner then retrieved its recorded Meta media with the existing read-only diagnostic tool:

* SHA-256 **`d7cd371fc8c6d4fadb636cb786358478f9a2553247f50591bfada6325c74dabb`**, **67,427 bytes**.
* Meta metadata hash/length match downloaded bytes; downloaded bytes match the retained current file.
* Metadata MIME **audio/ogg**; actual stream **Opus, mono, 48,000 Hz**; decoded/rounded duration **7.296 seconds** (container duration 7.3023958333 seconds).
* Historical `audio_trace`, delivery status and error are NULL; `expected_upload_matches` is unknown. Do not manufacture a historical multipart trace, even though the original selected Meta media and current retained file agree now.

Failed attempt 56 is reported **29,751 bytes** at approximately the same decoded duration. The approximate container bitrates are 73.9 kbps vs 32.6 kbps; this is a concrete difference worth investigating, **not proof of a codec defect or of identical source content**. Different original recordings and VBR can affect size. The working file also uses audio/ogg, which gives no evidence that adding `codecs=opus` to the MIME label will fix the issue.

Saved Voices merge `7dedda2` is dated 27 Oct 21:47:34 Sri Lanka time, shortly before the working send if its timestamp is UTC. This makes its historical implementation relevant, **not proof that Railway ran that SHA**. The new paired comparison can inspect the exact retained Meta working and failing media without sending again.

Fetched main was `119eccb` (merge PR #99). The owner subsequently supplied **live diagnostic evidence** reporting artifact and platform SHA `119eccbc681c8b11363d2e0c4547dac5ad4f49d3`, sources agreeing, artifact files matching, schema **20261030_21**, PyAV **16.1.0**, Graph **v26.0**, and **Global AI false**. The supplied runtime hashes for `audio_service.py`, `whatsapp_service.py`, `saved_voice_service.py` and `main.py` were independently recomputed from Git 119eccb and all match. This is materially stronger than assuming deployment from a push; it is still owner-supplied runtime evidence, not independent Railway control-plane access. The previous failing-runtime report was 6bb39f7/schema20, not a verified first failing deployment. No SHA has yet been tied to the original working send.

Evidence needed to bound a regression:

1. Received: CRM message1078 / attempt1, still playing on Android, timestamp and matching Meta-file hash. Its old deployment SHA and detailed actual-file packet evidence remain missing. A native message from a different sender would not substitute for this CRM baseline.
2. One first-failing CRM message with the same identifiers and approximate time.
3. Railway active and previous deployment details at those times, including actual artifact SHA, Python/runtime/build image information, replica identity and library versions. Compare existing runtime diagnostics with Railway deployment records. Git author/merge times alone do not prove deployment.
4. Owner-authorized original/Meta-hosted files where still available (the old reference's Meta report is now supplied, but not its raw bytes in this workspace). Historical messages need not and must not be modified or resent.

## Git differences affecting audio

Times below are Sri Lanka time; they are Git author/merge times, **not Railway deployment times**.

| Change | Time / evidence | Audio implication |
| --- | --- | --- |
| Historical `audio_service.py` | Same blob at `96eae5c` (20 Oct 11:19) and `22b05f8^`; SHA-256 `55e70f71cbe8b7482484ccaba8a19dd0eec3623829ebedc9fce565e8b7a4a622` | Mono 48 kHz libopus, `fltp` resampler, bitrate/application/frame options left to defaults; flushes resampler/encoder and closes OGG muxer. Not proof that this file played on WhatsApp. |
| `22d0a7c` | 23 Oct 09:54 | Nixpacks adds C++ runtime and wheel-only PyAV installation. Could affect build environment, but predates the claimed yesterday/today boundary. Actual deployed image still required. |
| `22c1b46` requirements | 24 Oct 20:08 | Adds `av==16.1.0`. No version bump in the subsequent normalization fix. No full dependency lockfile/build-image digest is present to prove all historical transitive versions. |
| `5159e4c` | 27 Oct 21:46 | Persistent Saved Voices library and durable send outcomes. Uses shared normalizer/upload/send helpers; media ID return/outcome semantics extended. |
| `2d57a9c`, merged `7749799` | Merge 29 Oct 09:25 | Adds global AI barriers; manual voice sends retain their manual context. No encoder/MIME change. Existing tests cover manual sends with AI OFF. Do not roll back this security control speculatively. |
| `22b05f8`, merged `a9a027c` | Author 30 Oct 14:57; merge 15:34 | Changes `fltp`→`flt`, sets 32 kbps, application `voip`, 20 ms frames and VBR on; adds byte-based detection, full validation, legacy preparation, retained originals and schema19. PyAV pin remains 16.1.0 (requirements comment only). |
| `1d85a53`, merged `6bb39f7` | Merge 30 Oct 16:29 | Adds runtime and byte evidence/schema20; build artifact provenance command. No encoder change. |
| `dfa1f03`, merged `119eccb` | Merge 30 Oct 18:58 | Adds explicit owner-only A/B tooling/schema21. No production encoding or upload MIME change. |

Crucially, `22b05f8` was created to address an **already-reported** playback failure. Its encoding differences are worth comparing, but cannot be assumed to explain the original defect. There may be more than one issue or an earlier unverified environment/transport change. Never restore the old unsafe input validation as a presumed regression fix.

Git comparisons of `22b05f8^` and current sending show the same actual upload contract: default filename `voice-note.ogg`, multipart `type=audio/ogg`, file-part Content-Type `audio/ogg`, and genuine OGG bytes. The `/media` upload and `/messages` send both use the configured business phone ID and Graph version. `audio.id` comes from the upload response; voice sends add `voice:true`. Diagnostics/idempotency changes did not introduce a MIME relabel. Graph's configured default `v26.0` predates this incident; actual Railway environment changes are unknown.

## Meta restrictions and official contract

**No affirmative account/phone restriction evidence is available.** HTTP200/delivered and no supplied relevant numeric error are not evidence of a documented restriction; neither do they conclusively rule out a provider-side media issue. No account settings, number registration or appeals were changed. No speculative Graph account-field queries were added when their current contract could not be verified.

Official references to inspect with authenticated browser access:

* https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media
* https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages#audio-object
* https://developers.facebook.com/docs/whatsapp/cloud-api/messages/audio-messages
* https://developers.facebook.com/docs/whatsapp/cloud-api/support/supported-media-types
* https://developers.facebook.com/docs/whatsapp/cloud-api/phone-numbers
* https://developers.facebook.com/docs/graph-api/changelog/
* Opus packet duration/framing: https://www.rfc-editor.org/rfc/rfc6716 (section 3.1)
* OGG Opus headers, granules and pre-skip: https://www.rfc-editor.org/rfc/rfc7845

On 30 October the official Meta media and phone-number pages returned proxy CONNECT **403** from this environment. Earlier direct attempts failed DNS. Thus **current official media requirements, parameterized MIME acceptance, current voice-flag support, registration fields and version-specific restrictions remain unverified here**. We do not assert that `audio/ogg; codecs=opus` is required, and did not change production MIME based on examples. Observed successful Meta acceptance of `audio/ogg` is an observation, not complete documentation verification. AAC/M4A uses the candidate offline label `audio/mp4`; it is not enabled for Cloud API sending by this change.

The multipart regression test builds the real HTTPX request, parses the wire file part, and checks filename, MIME, exact bytes, form fields and returned media ID. Existing A/B tests cover audio payload ID and omission/presence of `voice`. Diagnostics now retain the safe parameterized Meta metadata form `audio/ogg; codecs=opus` rather than hiding it as unrecognized; this changes only evidence display, not outbound headers.

Administrator read-only checks in WhatsApp Manager (select the exact business account and connected phone):

1. Check Phone numbers → current connection/registration status, quality rating, messaging limits and any restriction/warning banners. Check Business Support Home / account status for explicit enforcement details and timestamps.
2. Compare number registration/reconnection and business/account changes with the last-working and first-failing send times. Do not register another number or change configuration while investigating.
3. Collect relevant numeric media/delivery error codes and safe status labels, if any, alongside message IDs and runtime evidence. A quality color or messaging limit alone does not establish why delivered media fails playback.
4. If there is an explicit restriction, record its exact category/date privately and consult its official explanation. Do not infer restriction from the waveform, phone error, or inability to download alone.

## Offline stream comparison

A synthetic 7.300-second, 48 kHz mono PCM tone (not customer audio) was encoded using the historical code extracted from Git, current PyAV code and independent system FFmpeg/libopus. PyAV 16.1.0 bundles libavcodec 62.11.100/libavformat 62.3.100 in this workspace; independent FFmpeg/ffprobe are 7.1.5-0+deb13u1. `opusinfo` is not installed. This reproduces code profiles using the **current** PyAV wheel, not a recovered historical Railway environment.

| Output | Bytes in this run | OpusHead / channels / input rate | Pre-skip / gain / mapping | Pages / packets / final granule | Encoded packet duration | Independent decode |
| --- | ---: | --- | --- | --- | --- | --- |
| Historical pre-22b05f8 profile | 75,422 | 1 / 1 / 48,000 | 312 / 0 / 0 | 10 / 368 / 350,712 | 20 ms | Passed, no warnings |
| Current profile | 42,803 | 1 / 1 / 48,000 | 312 / 0 / 0 | 10 / 368 / 350,712 | 20 ms | Passed, no warnings |
| Independent FFmpeg 32k/voip | 41,558 | 1 / 1 / 48,000 | 312 / 0 / 0 | 10 / 368 / 350,712 | 20 ms | Passed, no warnings |

The historical synthetic output uses CELT (TOC config31); current and independent matched-parameter output use hybrid/CELT (configs13/15/27). Those are valid Opus modes, not evidence of malformed audio. The report now records mode/config distributions so actual-file comparisons can establish whether that difference exists in production.

All three had OpusTags, monotonic granules, valid CRC/page structure, EOS and a decoded duration of 7.300 seconds. `(350712−312)/48000 = 7.3`. Demux reports a 6.5 ms boundary packet because of pre-skip/end trimming; its **encoded** Opus packet is still 20 ms. The earlier diagnostic duration field alone could be misread, so this branch adds TOC-derived packet durations, separate demux interpretation, page granules and private OpusTags structure/hash evidence. No stream correction is justified from this observation.

Hashes and byte lengths are captured per generated file, but OGG serial numbers can make complete file hashes vary across encodes; identical packet-content comparison is used for the historical-code regression test. Size differences do not prove incompatibility. Attempt 1 is now owner-confirmed to play on Android, with a matching Meta/retained-file hash supplied. Its raw audio bytes and detailed TOC/Opus headers have not been retrieved into this workspace, so an independent actual-file comparison remains pending. The independent file is standards/decode evidence, **not a WhatsApp-known-good sample**. The tests also generate and independently decode mono AAC/M4A from the same source, without uploading or sending it.

## Implementation and exact owner steps

The existing A/B send path stays unchanged. This branch adds **offline-only** comparisons and exact-media read-only inspection:

1. Deploy this branch after review. Verify actual runtime provenance against Railway. Existing schema remains **20261030_21**; no migration or dependency/build-image change is required. Leave Global AI OFF; do not change learning configuration.
2. With the existing owner-test feature explicitly enabled/configured, ADMIN opens `/crm/bot-management/voice-diagnostics/ab?run_id=<existing-run-id>`. Prefer the run already confirmed to fail; do not create another send solely to obtain this report.
3. First use **Compare existing owner media — send nothing**: working route Saved Voices, attempt **1**; failed route Saved Voices, attempt **56**. Confirm read-only owner authorization. Both stored attempts are checked against the configured owner recipient before any Meta GET; customer references fail closed. The tool downloads/analyzes the exact media, removes private temporary files, and stores the redacted paired report in the selected A/B run. Share that report, especially header/TOC/mode differences. No messages are sent.
4. Optionally confirm owner authorization and click **Analyze offline — send nothing**. This verifies the retained source and immutable snapshot hashes, compares current/historical/AAC profiles and independent FFmpeg if installed, stores only the redacted report in that run's evidence, and removes temporary audio files. No Meta call occurs. A 0.25–30 second source is required; changed or missing originals fail closed. No phone outcome is inferred from the snapshot.
5. Review the comparison table and expanded evidence. Optional FFmpeg/ffprobe absence is explicitly reported; PyAV's bundled FFmpeg is not labeled an independent decoder. No Railway dependency is automatically installed.
6. Provide the paired report and, if available, the Railway deployment reference for the known-working message. This is the next evidence needed before choosing a correction.

For an operator with a Railway shell, the **existing** bounded/trusted-host Meta GET retrieval can now pass the exact bytes to offline inspection and save them only in a private directory:

```sh
python -m voice_regression_diagnostics meta --route saved --attempt-id 1 --owner-authorized --output-dir /data/voice-diagnostics
python -m voice_regression_diagnostics meta --route saved --attempt-id 56 --owner-authorized --output-dir /data/voice-diagnostics
python -m voice_regression_diagnostics meta --route browser --attempt-id 1422 --owner-authorized --output-dir /data/voice-diagnostics
```

Run only for these already owner-authorized tests or another explicitly authorized owner test. This requires the running service's existing Meta credentials and database access; never paste credentials or environment dumps. CLI use requires a privileged authenticated Railway/operator shell; it is not a public diagnostic endpoint. No token is a command argument. The parent must belong to the app user with permissions 0700 (new directories are created private). The command creates a unique subdirectory, saves `meta-hosted.audio` and a redacted `meta-report.json`, and prints only redacted JSON plus the generated workspace identifier. Files are mode 0600, not served publicly. It uses the recorded media ID, retrieves exact hosted bytes, compares historic upload hashes where present, and performs independent decoding if tools are available. Expired/denied media is reported, not silently replaced with today's Saved Voice file. It never uploads or sends.

For a paired, read-only structural comparison of the newly supplied old reference and failed attempt 56, an authorized operator can run:

```sh
python -m voice_regression_diagnostics pair --working-route saved --working-attempt-id 1 --route saved --attempt-id 56 --owner-authorized --output-dir /data/voice-diagnostics
```

The command creates separate private subdirectories, downloads/analyzes each exact media ID, and records header/packet-field agreement or unknown values if retrieval fails. For attempt 1, Android playback is owner-confirmed; the tool still treats reference labels as owner-provided rather than inferring playback from HTTP/bytes. These two recordings have different source content, so unequal hashes/bitrate/duration alone are not encoding defects. It sends no messages.

A private workstation with FFmpeg/ffprobe can compare an explicitly owner-authorized source and the retrieved files:

```sh
python -m voice_regression_diagnostics compare --owner-authorized --source /private/owner-source.m4a --actual-failed /private/meta-hosted.audio --known-working /private/owner-working.ogg --output-dir /private/voice-diagnostics
```

`--known-working` and `--actual-failed` are optional owner-provenance labels; the tool cannot certify the labels. Use the same original short source for experiments, not an already lossy output when the original exists. Existing source files are read without following symlinks and never changed. Generated outputs/report are private; decoder stderr is represented only by presence/length/hash, never tags, paths or signed URLs. No raw comments/vendor text enters JSON. Optional tools time out and input duration/size are bounded. No automatic network operation occurs in `compare`.

**Do not upload these candidate files through Saved Voices for a supposed untouched-format comparison:** that route normalizes again. This branch adds no live AAC/historical/FFmpeg send route and no bypass of existing owner-only A/B security. Any further live encoding experiment needs current official format verification, a separately reviewed immutable candidate, verified owner destination and fresh explicit send confirmation. No customer sends or automatic retries are permitted. Existing A/B replay protection, recipient verification, authorization and confirmation remain intact.

## Files, preservation and rollback

* `voice_regression_diagnostics.py`: bounded offline historical/current/FFmpeg/AAC comparisons, optional known-working/failed file analysis, private artifacts and read-only existing Meta attempt inspection.
* `voice_compatibility.py`: TOC packet duration vs demux trimming, granule duration/page evidence and OpusTags structure without private comments.
* `voice_production_diagnostics.py`: optional internal offline inspection callback after bounded Meta retrieval; safe parameterized MIME display.
* `voice_playback_routes.py`, `templates/voice_playback_test.html`: ADMIN/CSRF/owner-confirmed offline and paired exact-media actions and report table, gated by the existing experimental feature switch.
* `build_info.py`: artifact provenance includes the new diagnostic module.
* `tests/test_voice_regression_diagnostics.py`: offline, private storage, profile/packet, MIME/multipart and authorization regression tests.

No production encoder, MIME, message payload, media cache, original source, historical customer message, AI activation/learning configuration, accounting feature or Railway configuration changes. No destructive migrations. To disable experiments, set the existing `VOICE_PLAYBACK_TEST_ENABLED=false`; this blocks new offline web analyses and existing A/B sends. To roll back the release, redeploy the previous verified artifact, retaining schema21 and the original media volume. The additive JSON evidence does not require rollback or deletion. CLI-created private diagnostic artifacts need a deliberate retention decision; remove only the diagnostic directory after investigation, never Saved Voices originals.

## Correction decision

Current confidence: high that the voice flag alone is not sufficient (owner A/B evidence); high that the compared synthetic streams are independently decodable; **low/unknown for the production root cause**. No account restriction or objective muxing/MIME defect has been demonstrated. A working Meta-file reference is now identified; its detailed headers/packets, the exact failed-file comparison and a working build/deployment boundary are still needed. If independent decoding fails on the exact failed bytes, isolate that defect; if it succeeds, compare historical working packets/runtime and provider/client handoff before changing encoding. Android playback of the final candidate remains the acceptance test. No fix is claimed from HTTP200, delivered status or automated tests.

## Validation results

* New diagnostic suite: **17 passed** after final evidence-field changes. Includes source/size/duration protection, private files, historical packet-content equivalence to the Git profile, exact multipart bytes/filename/MIME, parameterized Meta MIME display, TOC vs demux interpretation, private OpusTags evidence, exact read-only media pairing, unavailable-media unknowns, no send/upload side effects, role/CSRF/confirmation checks, customer-reference rejection before Meta access, and unchanged source files/Global AI OFF.
* Combined focused suite: **198 passed, 1 deselected** with isolated PostgreSQL and mocked Meta calls. Command: `python -m pytest -q tests/test_voice_regression_diagnostics.py tests/test_voice_playback_test.py tests/test_voice_production_trace.py tests/test_voice_audio_compatibility.py tests/test_audio_service.py tests/test_saved_voices.py tests/test_whatsapp.py tests/test_global_ai_control.py tests/test_learning_migration.py tests/test_crm_whatsapp_inbox.py -k 'not test_inbox_requires_authentication_and_renders_for_staff'`. Includes existing concurrent A/B claims, duplicate-send prevention, partial/unknown outcomes, browser voice recording, Saved Voices playback and Chromium confirmation/results workflow. Final metadata-only evidence additions were then rechecked in the 17-test suite.
* Known baseline failure re-run separately: **1 failed** (`test_inbox_requires_authentication_and_renders_for_staff`). It expects the obsolete JavaScript cache string `inbox-colombo-time-1`, while main's existing Inbox template uses `saved-voices-1`. This mismatch predates this branch. It was not hidden with an unrelated production change; the combined run deliberately deselects it and this report discloses it.
* Production-only Python 3.12 environment: dependency check and compilation pass. Real Uvicorn startup/restart checks passed on SQLite and PostgreSQL with two instances, normalized audio persistence, the owner tool disabled by default, and Global AI OFF retained. Real provider credentials were removed in these checks. No live WhatsApp messages were sent.
* No production raw audio was accessed in this workspace. Actual old-media/failed-media properties above come from owner-provided reports; the synthetic independent decode comparison is separately labeled. Android playback is still required for any future corrected candidate.
