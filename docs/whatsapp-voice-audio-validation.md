# Saved Voices: validated OGG/Opus and production playback verification

## Investigation result and remaining uncertainty

The pre-change upload route already used PyAV (FFmpeg/libopus) to convert audio
and stored the result, not just a renamed M4A. Its Meta upload used `audio/ogg`;
the message payload used `type: audio`, an uploaded media ID, and `audio.voice:
true`. Each explicit send uploaded bytes afresh. There was no SavedVoice-level
cached Meta media ID. Storage checked a SHA-256 checksum but not the actual
container/codec. The encoder output was not reopened and decoded before sending.

A synthetic 7.3-second M4A/AAC recording passed through the old converter was independently
identified by ffprobe as OGG/Opus, mono, 48000 Hz, and fully decoded. Consequently
**M4A being relabeled as OGG is not a verified root cause**. The failing owner's
`Voice 036.m4a`, its production-stored bytes, and an actual failing Meta media
response were not available in this workspace. The precise phone-playback cause
remains unverified. This change closes verified validation/diagnostic gaps; it is
not proof that the owner's WhatsApp playback problem is resolved.

The target wire format is a genuine OGG container, Opus codec, mono, `audio/ogg`,
at most 16 MiB. The encoder explicitly uses 48000 Hz, 32 kbps VBR speech mode and
20 ms Opus frames. The existing native-voice flag is retained; ordinary audio
(`voice=False`) omits it. Public references:

- https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media/
- https://developers.facebook.com/docs/whatsapp/cloud-api/messages/audio-messages/

Public Meta documentation could not be retrieved from this workspace (network
ProxyError), so live documentation verification is not claimed. The configured
Graph API version is unchanged. The actual current Meta/phone behavior must be
verified on the owner's controlled test number as described below.

## Conversion and byte validation

`audio_service.py` detects supported audio from signatures and libav's actual
container/stream data, independently of filename and browser MIME. Supported
inputs include M4A/AAC, raw AAC, MP3, PCM WAV, OGG Opus/Vorbis and WebM audio.
Mismatched labels on genuine supported audio are normalized; random data,
playlists, videos, multiple audio streams, unsupported codecs, empty recordings,
oversized recordings and undecodable files are rejected. Input probing is from
memory with restricted protocols. Limits remain 16 MiB and ten minutes; minimum
usable duration is 0.25 seconds.

The server decodes, resamples to mono 48 kHz and encodes through libopus. It then
validates OGG page boundaries, sequence, stream serial, CRC checksums, OpusHead
channel mapping and final EOS marker. A separate full decode checks codec,
channels, sample rate, duration, size and agreement with decoded source duration.
This is genuine transcoding, not renaming or setting MIME labels. The Meta upload
helper independently validates the exact bytes immediately before upload. Both
Saved Voices and existing browser-recorded voice messages use this implementation.

## Storage, existing recordings and UI

Migration `20261030_19` adds validation state/source retention metadata and safe
delivery diagnostic fields. Existing recordings and send claims are preserved;
there is no mass conversion, deletion or resend during migration. Existing rows
start PENDING. In Saved Voices, use **Prepare recording** to normalize them. A
first direct send of a legacy row also prepares it but returns 409: staff must
refresh and preview the new revision before an external upload/send is permitted.

New uploads show Processing while conversion runs; failures return a clear
Conversion failed message and create no sendable recording. Successful uploads
are Ready to send and have a normalized server preview. Legacy conversion has
persistent PROCESSING/READY/FAILED state; failed recordings cannot be selected or
sent. The library also displays source/output audio details. Ready means format
validation passed, not that a recipient has confirmed WhatsApp playback.

Original files are retained privately, including each new raw upload (`.source`)
and each pre-existing file before replacement. They are never deleted by this
migration or preparation. Normalized files use generated `.ogg` names, private
permissions and hash/size integrity checks. There is no public original-file URL.
Preparation updates the normalized pointer and increments the recording revision;
existing send history is retained. Fenced database claims prevent simultaneous
workers from publishing competing conversions. A failed/crashed conversion lease
can be retried explicitly after two minutes. Retained source can rebuild a corrupt
normalized copy. Ambiguous DB commits may leave private orphan files; they are
not deleted automatically because a commit may have succeeded.

## Sending, idempotency and diagnostics

The exact validated stored bytes are uploaded as `audio/ogg`, then the returned
media ID is sent in an audio message with the native voice flag. No transcript is
sent as a separate text. A new explicit attempt obtains a fresh media upload;
repeating an existing idempotency key only reads its result and never resends.
An uncertain send remains uncertain and must not be automatically retried.

New audio diagnostics log source format/codec/rate/channels/size and decoded
duration, normalized format/codec/duration/size, HTTP upload/send results, media-ID
presence and numeric Meta error/suberror codes. Delivery webhooks persist status
and numeric error code on SavedVoiceSend and update normal message delivery state.
They do not log private audio, filenames, transcripts, customer phone numbers,
tokens, provider error messages or credentials. Upload success, a message ID,
and even delivered/read status do not prove playable audio on WhatsApp.

Global AI settings, per-conversation ownership/modes, learning settings, Quick
Messages, product cards, customers, orders and accounting are not modified. Manual
voice sending remains independent of Global AI OFF. No customer messages are sent
by migration, preparation, preview, deployment self-checks or automated tests.

## Railway deployment

1. Keep customer use of Saved Voices paused until the controlled phone test passes.
   Merge/deploy the dedicated branch to the web service and drain old web revisions before using the new validation. Leave Global AI OFF and
   existing learning configuration unchanged; no Publish action is needed.
2. Startup runs the existing migrations; verify Alembic head is `20261030_19`.
   An explicit deployment migration may use `python -m alembic upgrade head`.
   No provider credentials or existing data need changing.
3. Keep the existing `SAVED_VOICES_DIR` on persistent storage (default
   `/data/saved-voices`, with the app volume mounted at `/data`). Back up the
   database and volume together. Retaining originals increases disk use.
   Do not increase replicas without shared audio storage: a Railway service volume
   is not automatically a shared filesystem across separate replicas/services.
4. `requirements.txt` already pins `av==16.1.0`. Its CPython 3.12 manylinux wheel
   bundles FFmpeg codec libraries, including AAC decoding and libopus encoding.
   A system `ffmpeg` executable is not required. Do not replace the wheel with a
   source build lacking libopus. Run `python -m audio_service --check` in the
   deployed environment: it performs a synthetic WAV → OGG/Opus → full decode
   and prints only format metadata; no Meta request or private file is involved.
5. Verify the Inbox, manual text/Quick Messages, product cards and Global AI OFF
   indicator still work. Prepare **one test recording**, not the whole library,
   and preview the normalized result. Preparing does not contact a customer.

## Required owner-phone acceptance test — still pending

Use the owner's explicitly confirmed test WhatsApp number, with the normal Meta
customer-service window open. Do not infer that number from customer screenshots.

1. Upload the original `Voice 036.m4a` as a **new Saved Voice**. Confirm Ready to
   send and source AAC → normalized OGG/Opus, mono/48000 Hz; listen to the complete
   normalized preview. Keep the original upload for comparison.
2. Select that recording in the owner's test conversation and click Send Voice
   **once**. Check its delivery state/error code; do not automatically retry an
   uncertain result. Meta upload acceptance alone is insufficient.
3. On the owner's actual WhatsApp phone, press Play, listen through the entire
   approximately 7.3-second recording, and seek/replay it. Confirm the woman's
   voice is intelligible, complete, and no unavailable/invalid-audio warning
   appears. Compare against the original and CRM preview. Record phone OS,
   WhatsApp version, time, internal recording/send IDs and any numeric error code;
   keep phone numbers and private content out of shared diagnostic logs.
4. Also test one existing recording after Prepare and one browser-recorded voice
   on the same controlled number. Only resume staff customer voice sends after
   the owner confirms actual playback. Keep Global AI OFF throughout.
5. If playback still fails, keep customer voice sends paused. Preserve the original
   and normalized bytes, internal attempt identifiers and numeric diagnostics for
   comparison. Do not claim the issue fixed or repeatedly resend to customers.

## Tests and files

Regression coverage uses real AAC/M4A, raw AAC, WebM and OGG/Opus encoders and
decoders; corrupt/truncated/checksum-invalid data, empty and unsupported files,
MIME mismatches, encoder failure, normalized previews, retained-source recovery,
legacy leases/concurrency, fresh Meta uploads and idempotency, native versus
ordinary audio payloads, safe delivery diagnostics, browser playback, the existing
browser recorder, Global AI OFF, and PostgreSQL migrations. Meta is mocked.
Browser decoding and ffprobe validation supplement these checks; neither replaces
the required actual WhatsApp phone playback test.

Implementation files: `audio_service.py`, `saved_voice_service.py`,
`saved_voice_routes.py`, `whatsapp_service.py`, `main.py`, `models.py`,
`alembic/versions/20261030_19_voice_audio_validation.py`, `requirements.txt`,
`static/saved_voices.js`, `templates/saved_voices.html`,
`templates/_saved_voice_picker.html`. Tests are in
`tests/test_voice_audio_compatibility.py`, with existing Saved Voices, WhatsApp,
global-control and migration regression expectations updated for schema head 19.
