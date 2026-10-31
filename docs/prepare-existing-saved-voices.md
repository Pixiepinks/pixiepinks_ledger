# Prepare existing Saved Voices without deleting them

The explicit **Prepare recording** action now works while staff FFmpeg is ON.
No bulk migration runs at startup, no messages are sent by preparation, and the
recording keeps its ID, title, category, language and transcript.

For legacy valid OGG, the existing validated bytes feed the staff FFmpeg pipeline.
For an old actual M4A/AAC/WAV mislabeled by its extension, preparation uses the
existing byte-detected PyAV normalization as a private OGG staging step. That
staging output is then encoded and validated by the normal Variant C FFmpeg
path. This deliberately follows the demonstrated working READY-OGG send path;
it does not send PyAV output as a fallback. If final FFmpeg conversion fails, the
recording remains failed and cannot be selected. Originals are never deleted.

The prior source-integrity check, processing-slot limit, cross-worker lease/token
fence, resource limits and revision checks stay in place. A successful explicit
prepare creates a new normalized file, preserves the original storage reference
and increments revision. Staff must preview and select the updated revision.
Repeated preparation of an already-current READY recording only validates it;
it does not repeatedly convert it or increment its revision.

Sending an unprepared legacy entry remains blocked with a clear instruction to
click Prepare recording. Preparation status/errors remain visible after the
library refresh instead of being overwritten. Both script references are versioned
so browsers fetch the updated UI.

Deploy the reviewed commit; no migration, new package, flag or AI setting change
is required. Keep VOICE_FFMPEG_ENCODER_ENABLED=true and Global AI OFF. In the Inbox
or Saved Voices library, click Prepare recording on an old entry, wait for Ready
to send, preview, then Choose recording. Start with one old entry and verify it on
the owner's phone. Proceed through other old entries individually. Re-upload is
needed only if the source is missing, corrupted or cannot be decoded; failed
preparation does not remove the existing recording.

Rollback the code if needed; newly prepared normalized files and preserved
originals stay intact. No reverse data migration is required. Preparation does
not itself establish phone playback; actual receipt/playback is checked after
a separate authorized staff send.

Validation: 81 passed, 2 skipped across explicit legacy preparation, Saved Voices,
audio compatibility, existing candidate and staff workflows. New regressions
cover M4A/OGG/WAV, unchanged titles/transcripts/originals, no sends during prepare,
revision review, idempotent READY preparation, failed final encoder without
fallback, corrupt sources, and Global AI staying OFF. Provider calls were mocked;
no production preparation, sends or deployment occurred here.
