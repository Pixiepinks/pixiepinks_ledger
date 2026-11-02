# Browser Record Voice Message: match the working Saved Voice input

The owner confirms two Saved Voices play on the recipient phone, while an Inbox
Record Voice Message plays in CRM but not on that phone. The exact failed browser
message trace was not provided, so no particular Meta processing error, payload
regression or packet defect is asserted as the confirmed root cause.

Repository inspection shows the browser sends WebM/Opus, OGG/Opus or M4A/AAC
directly to the final encoder. Working Saved Voice sends feed fingerprint-checked,
validated OGG/Opus to that same final FFmpeg encoder. At the owner's request this
patch gives browser recordings the same validated OGG stage first, then uses the
unchanged normal final FFmpeg encoder, independent validation and ordinary audio
payload. Staging is private memory, never an uploaded fallback. Failure in either
stage rejects the request before upload or sending.

Only audio_service's new browser staging helper and the browser send route are
changed. Saved Voices preparation, uploads, existing originals, library revisions,
send snapshots and working send implementation are untouched. When the FFmpeg
flag is OFF no staging is added and existing rollback behavior stays the same.
Global AI and learning settings are unchanged. Browser audio remains in memory;
this patch does not alter existing CRM playback or add persistent microphone files.

The existing per-attempt trace now captures original browser fingerprint, staging
OGG fingerprint/validation, final encoder input/output, and the actual upload
fingerprint. No audio contents, transcripts, phones or credentials are logged.

Deploy reviewed commit, leave VOICE_FFMPEG_ENCODER_ENABLED=true and Global AI OFF.
No dependencies, migrations or frontend changes are required. Open the Inbox and
record one new short sample, preview it, and explicitly send it to the owner's
test conversation. Confirm download/playback on Android, not just CRM playback
or API submission. Preserve the new trace and detailed delivery errors if it
fails. Do not resend old failing messages automatically. This workspace has no
production or owner-phone access and has sent no live test messages.

Rollback by reverting this code change; do not change the working Saved Voices
configuration to roll back a browser-only change. No database rollback is needed.
Actual recipient-phone success remains a manual post-deployment verification.

Validation: 86 passed, 1 skipped across browser staging, production traces, staff
audio, Saved Voices and WhatsApp tests. New tests cover generated WebM/M4A/OGG,
genuine Chromium MediaRecorder output from a synthetic microphone, staging/final/
upload fingerprints, C codec-packet identity, ordinary payload, AI remaining OFF,
flag-OFF behavior and rejection without upload on either-stage failure. All
provider calls were mocked. The skip was not counted as a passing check.
