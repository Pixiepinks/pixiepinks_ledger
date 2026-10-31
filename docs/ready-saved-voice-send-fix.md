# READY Saved Voice send rejected with 422

Owner-provided Railway logs identify running artifact/platform commit
76a94b81c04120cbfec80bcd107eb23cd4f1d32d (the merged staff rollout), agreeing file
hashes, and three send-saved-voice requests rejected with HTTP 422. Each has
`audio_encoder_metric` outcome error, reason encode_failed, elapsed 469/634/468ms.
These requests failed before a media upload or message submission. This is not a
pending delivery or an AI OFF restriction. Production was not directly accessed.

The rollout's send code reopened the retained original, despite the library
already holding validated OGG/Opus. The exact Variant C pipe command can fail for
seek-dependent M4A. A real synthetic M4A with trailing moov >32KiB reproduces this:
direct C conversion fails, the existing library normalization produces valid
OGG, and C conversion of that validated OGG succeeds. Logs alone do not expose
this owner's exact source/container bytes; the input failure mechanism is
reproduced locally rather than independently proved on the private recording.

The fix feeds fingerprint-checked library OGG to Variant C during Saved Voice
sending. Encoder parameters and ordinary-audio payload are unchanged. Original
recordings, library files/revisions and history remain intact. Sent-byte snapshots
and duplicate-send claims remain. New M4A uploads still have the separately
documented pipe-input limitation; this change fixes sending READY library items.

The frontend previously reloaded the library after a 422, overwriting its error
with the normal library status. It now keeps the error visible, scrolls to it,
clears the rejected selection/pending key and says “Nothing was sent.” It does
not poll or retry. A script version bump prevents reuse of cached old behavior.

Tests use generated audio and mocked provider calls: M4A failure reproduction,
READY OGG success, preserved originals, C codec packets, exact sent-byte Inbox
preview, ordinary payload, no duplicate send, conversion failure before upload,
and a Chromium UI regression verifying visible persistent HTTP-422 feedback.
Focused reproduction/UI suite: 5 passed. Broader Saved Voices/staff/candidate/
WhatsApp suite: 84 passed, 1 skipped. All provider calls were mocked; the skipped
check was not counted as passing.

Deploy the reviewed fix to Railway and verify its commit. Keep the already
authorized VOICE_FFMPEG_ENCODER_ENABLED=true for staff and Global AI OFF.
After deployment, open the Inbox afresh, select the READY item and make one normal
staff send to the owner test conversation. Confirm receipt/playback separately.
Do not repeatedly submit any ambiguous prior attempt. The supplied 422 requests
did not create send attempts; they failed during preflight.

No migration, encoder switch, customer messages, merge or deployment performed
here. Roll back this code commit if needed, but the prior code may reject the same
READY item again. Setting the FFmpeg flag false restores the older PyAV/native
path, whose phone playback remains unproven; preserve evidence and pause voices
if delivery remains faulty.
