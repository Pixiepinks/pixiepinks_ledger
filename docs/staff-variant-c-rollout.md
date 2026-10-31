# Variant C for normal staff audio

The owner authorized replacing the owner-only restriction with normal staff use.
This change reuses the independently pinned FFmpeg 7.0.2 tool and Variant C codec
command, pipe input and ordinary audio payload. Successful historical C playback
is evidence for that file only, not proof that all new sources will play.

## Behavior

Set `VOICE_FFMPEG_ENCODER_ENABLED=true` after deploying this commit to select the
staff Variant C path. Flag OFF preserves the old PyAV/native behavior for rollback.
Global AI and learning settings are neither changed nor required for staff sends.
No owner-only flags or test-number configuration are required for normal staff.

- Saved Voices uploads and browser recordings use bounded independent FFmpeg
  encoding, full validation and independent decode; no silent encoder fallback.
- The C encoder command is unchanged except duration guard 31 to 601 seconds, to
  preserve the public 600-second limit. Source and output duration checks reject
  truncation. Short-source codec-packet equality against original C is tested.
- The prior candidate's sealed seekable input is removed. The original C pipe
  input is used. **Some M4A files with trailing metadata cannot be read by this
  path.** Such files are rejected before upload/send. Export as WAV or OGG and
  upload a replacement; no automatic alternative encoder is used. This limitation
  is explicit because exact C behavior does not support all formerly accepted M4A.
- Both normal staff pathways send ordinary audio with the voice key omitted.
  This may display as an audio attachment rather than a native microphone note.
- Existing Saved Voice records and original files are preserved. Each new send
  converts from retained original source after integrity checking (or the stored
  normalized file when no original exists). It stores a new immutable per-attempt
  snapshot, so Inbox history serves the exact uploaded bytes. It does not rewrite
  library revisions or earlier messages. Failed legacy validation still requires
  a replacement upload, not an automatic historical database migration.
- Replay of an existing send key returns its prior result without converting or
  sending again. Existing claim/window/role/CSRF guards remain.
- New Variant C staff attempts retain sanitized provider delivery error fields,
  including details, privately in audio_trace. API acceptance is not playback.

## Railway deployment

1. Review and merge the PR, deploy the reviewed commit. The Nixpacks build installs
   the existing hash-pinned imageio-ffmpeg 0.6.0 Linux x86_64 wheel and verifies its
   executable hash/version. There is no new migration or request-time download.
   Unsupported architectures or a missing/mismatching binary fail the build.
2. Set `VOICE_FFMPEG_ENCODER_ENABLED=true` on every web service handling staff
   audio. Verify running commit and audio self-check in Voice Diagnostics against
   Railway deployment details. Existing owner experiment runs remain archived.
3. Keep **Global AI OFF**. This switch is independent and no AI reply is enabled.
4. Send one normal staff Saved Voice to the owner's known test conversation and
   verify actual Android download/playback. Also verify a new browser recording.
   The owner has authorized use of the configured test number, but this workspace
   has no production access and has sent no live messages.
5. If either fails, preserve trace/error details, pause staff voice sending and
   investigate; do not treat upload or submission HTTP 200 as success.

## Rollback

Set `VOICE_FFMPEG_ENCODER_ENABLED=false` and restart/redeploy every web instance.
That restores the previous PyAV/native staff behavior; it is not a claim the old
path plays reliably. If playback remains defective, keep staff voice sends paused.
Stored originals and new per-attempt snapshots remain available; no data rollback
or re-encoding is necessary. Already submitted messages cannot be recalled, and
uncertain send attempts must not be retried with new keys.

## Validation

Audio/Saved Voices/candidate/exact-control suite: 91 passed, 2 skipped.
Staff diagnostics/Global AI/WhatsApp/Quick Messages suite: 90 passed, 2 skipped.
Provider calls were mocked. Tests cover C packet identity, ordinary payloads,
retained source/library integrity, exact sent-file Inbox playback, duplicate sends,
conversion rejection without uploads, detailed failure capture and flag rollback.
Railway TOML parsing and pinned executable verification also passed locally.
No production deployment, customer messages or live playback verification occurred.
