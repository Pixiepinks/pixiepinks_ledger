# Saved Voices: upload, preview and send

## Railway setup

The app service must have its own persistent volume mounted at `/data` (separate
from Postgres). Recordings default to `/data/saved-voices`; set `SAVED_VOICES_DIR`
only to use a different persistent directory. Local development/tests should set
it to a writable private directory. The app creates the directory on first upload
and reports a storage error if it cannot write. It does not fall back to a temporary
folder. Startup and the learning worker do not need to create audio files.

Deploy with the normal migration/startup process. Additive migration `20261027_17`
creates `saved_voices` and `saved_voice_sends`. Existing conversations, Quick
Messages, orders, AI settings and previously recorded voice messages are preserved.
There are no new dependencies and no Meta catalog or OpenAI configuration is needed.

## Upload a recording

Open **Saved Voices** in the sidebar. Administrators and sales managers can:

1. Enter a title, category, language and the written words spoken in the recording.
2. Choose an existing MP3, WAV, OGG, M4A or WebM audio recording.
3. Listen to the local preview and click **Upload recording**.

Transcripts are manually entered; this feature does not perform speech recognition
or generate a new voice. Uploads are decoded and converted in memory to mono 48 kHz
OGG/Opus for WhatsApp voice notes. The recorded speaker's voice is retained. Limits
are 16 MB before/after conversion and 10 minutes; malformed/non-audio inputs fail.
The complete multipart request is bounded, with two conversion slots per process.

Library cards display the title, category, language, duration, playback control and
written transcript. Search matches title, category and transcript, treating SQL
wildcards literally. Lists return 50 records at a time with Load more.

Managers can edit details or deactivate/reactivate a recording. Metadata edits use
revision checks; a stale editor must refresh. Audio files are immutable: to change
the recording itself, upload a new entry and deactivate the old entry. Deactivation
hides an entry from the active picker, but preserves old sent-message playback.

## Send from the inbox

Select a customer, click **Saved Voices**, search, listen/read and choose a recording.
The selected title/transcript and the target customer's name/number are shown before
the separate **Send Voice** button. Opening, searching, playing or choosing never
uploads anything to Meta or sends a customer message. Changing conversations closes
the picker; an already-started send remains bound to its original customer.

The send uploads the saved audio to Meta and sends it with `type=audio` and
`audio.voice=true`. The transcript is not sent as a text message. Inbox history
displays the audio and its saved title/transcript for staff, with the actual staff
sender and normal delivery/read receipts. Playback of saved voices in old inbox
messages reads the persistent local recording, so it does not depend on an expired
Meta media download URL. Ordinary existing voice playback is unchanged.

Administrators, sales managers, staff and sales agents may browse, preview and send.
Others cannot use the library APIs. All writes require the staff session CSRF token.
Manual sends follow the existing 24-hour WhatsApp customer-service window and do not
change AI/HUMAN mode. This is a staff-selected library; AI does not send these saved
recordings automatically.

## Storage, privacy and recovery

PostgreSQL stores titles, transcripts, immutable audio hashes, private filenames,
durations, revisions, staff attribution and send claims. Audio bytes live only in
the configured persistent directory, with generated filenames and private file
permissions. Audio is never served from a public static directory. Authenticated
playback supports byte ranges and returns private/no-store headers. Stored audio
must match its recorded size/hash; path traversal and symlink files are rejected.

Use both Postgres and app-volume backups to restore the complete library. Archiving
does not free audio storage, and there is no automatic expiry/retention deletion for
saved recordings. A single app volume assumes the current single-replica service;
multiple independent app replicas would need shared private object storage instead.
If a DB commit fails ambiguously after file creation, a private orphan file may be
left on the volume. Do not automatically delete files without reconciling database
references. Schema downgrade does not delete any audio files.

Each explicit send uses a unique durable key bound to staff, conversation, recording
and revision. The DB claim is committed before Meta I/O, with SENDING committed before
the customer-facing API call. Concurrent duplicate requests and repeated keys cannot
replay the send. Active state/revision and the service window are checked again after
Meta media upload. No message is sent if the recording changes or the window closes.

The browser saves its pending key in session storage before sending. On a lost HTTP
response, **Check send status** reads that attempt instead of issuing another send.
A known FAILED result allows a new explicit attempt. UNKNOWN outcomes and interrupted
SENDING claims are never automatically retried. Claims still pending after two minutes
are displayed as UNKNOWN without modifying/replaying them. Check the customer chat
and provider/server records before deciding to send a recording again. Database
failure after a provider send retains the claim, preventing a duplicate on retry.

## Verification

Tests use real local audio conversion and mocked Meta calls. They cover private
storage, range playback, transcript search, upload validation, roles/CSRF, stale
revisions, archive behavior, unavailable/tampered/symlink files, expired windows,
changes during upload, duplicate requests, post-provider DB failure and explicit
voice-only Meta payloads. PostgreSQL tests upgrade a populated prior schema twice
and verify concurrent send claims. Chromium checks selection without sending and
lost-response recovery without a second provider send.

Requirements-only Python 3.12 startup/restart checks upload an actual recording and
verify the same file and metadata remain usable after restart on SQLite/PostgreSQL.
No production customer messages or external AI requests are used during verification.

Results: 20 final Saved Voices checks passed, including Chromium and PostgreSQL;
45 checks passed with media-format regressions, and 109 broader focused checks
passed with four environment-gated skips in that invocation. The full suite ran
1,178 passing checks, two failures and two skips. The failures were the existing
payment-claim and Shopify-order-event PostgreSQL concurrency tests; both reproduced
on unchanged main `7e6956f` (the order-event failure is intermittent). Both
primary-PostgreSQL-only skips passed separately in disposable schemas. Requirements-
only Python 3.12.14 startup/restart, authenticated pages/assets, real upload persistence,
migration head `20261027_17` and disabled-worker diagnostics passed on both backends.
