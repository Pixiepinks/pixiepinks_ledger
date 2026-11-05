# New Saved Voice uploads: M4A input preparation

A valid M4A/AAC recording can play in a browser while failing direct FFmpeg pipe
input. In particular, trailing MP4 metadata may require seeking back to audio
packets that are no longer buffered. This is not determined by recording duration:
a valid two-second fixture with trailing metadata padding reproduces the same
conversion error, as does the existing longer trailing-moov fixture.

The new-upload route previously sent original bytes directly into the pipe-only
Variant C encoder when VOICE_FFMPEG_ENCODER_ENABLED was already on. The existing
browser-recording path and legacy Saved Voice preparation path already staged
input into validated OGG first. The upload route now reuses that same existing
stage_browser_voice helper before its final normalize_voice_audio call.

The final pinned FFmpeg profile, validation, codec parameters, rollout flag and
send payload are unchanged. There is no fallback if final FFmpeg fails. With the
flag off, staging is a no-op and the prior PyAV route remains. Original uploaded
bytes are still retained unchanged; source diagnostics describe those original
bytes. No existing library items are rewritten and saving never messages Meta.

This reproduces and fixes an applicable upload-path bug; the owner's exact file
was not provided, so its individual failure has not been independently reproduced.

## Deploy and rollback

Deploy the reviewed commit normally. No migration, new dependency or environment
change is needed. Do not toggle Global AI or the production audio flag. Retry
saving the existing M4A in the library; do not send a customer message to verify
an upload. Existing recordings do not need deletion or re-upload.

Rollback restores the preceding application version. No database rollback is
required. Newly saved files use the same existing validated OGG storage format.

Validation: 34 checks passed, 1 optional integration check skipped across staff
Variant C, Saved Voices and browser voice staging. New cases cover short padded
M4A and longer trailing-moov uploads with FFmpeg already enabled, original-byte
preservation, independent output decoding and final-encoder failure without a
fallback or partially saved library item. All provider transport was mocked.
