# One owner-only ordinary-audio control

Purpose: compare ordinary delivery with failed native candidate
`def27d027c1742be8f4baa8e8e358efb`, without changing its encoded bytes.

## Fixed evidence and safeguards

The only permitted reference is the above run, owned by the signed-in ADMIN and
bound to the configured owner/business/Graph version. Its original P variant must
show native `voice:true`, matching media ID, failed delivery and error 131053.
Its retained immutable output must equal the reviewed upload fingerprint:

```
SHA-256 b2f13701d7e62e9229afa53f9e866e7791073eecbdfedd85d1230607fe5243b2
41,771 bytes
```

Snapshot, validation and multipart historical fingerprints must also agree. No
encoder is called. Two new private snapshot copies are created for this control;
the failed run and Saved Voices originals are untouched. If the reference snapshot
was deleted, preparation fails closed. This implementation deliberately uses the
retained-snapshot option, not an automatic Meta download or library substitute.

A deterministic experiment ID allows only one control per reference across web
instances, administrators, reloads and repeated preparation. The existing primary
key handles concurrent preparation. One durable conditional REVIEW claim and one
variant claim precede network I/O. Unknown or partial sends are never retried.
Closing/deleting this control does not reset that fence. Expired review cannot be
re-created to produce additional attempts; investigate before any code change.

An explicit checkbox authorizes exactly one send; count must equal one. The
configured owner must have an open 24-hour service window at review and at each
network boundary. Owner binding and opt-ins are rechecked. Global AI must already
be OFF and the production FFmpeg flag OFF; this code never changes either value.
The normal application sender is used with `voice=False`, which omits the voice
key entirely, a fresh media upload, and a new `candidate:<control ID>:P` key.

The owner-only payload adds `biz_opaque_callback_data=exact-control:<control ID>`.
This allows a signed status callback to correlate while the submission request is
still in progress. Only a matching exact-control variant in SENDING with no
message ID may be linked. Late callbacks match the actual message ID normally.
Submission trace updates preserve any early delivery event. Concurrent callbacks
lock the variant row on PostgreSQL. Existing status signature verification stays
unchanged. As with existing routes, configure META_APP_SECRET in production.

The external send boundary remains non-transactional: a request accepted by Meta
can time out locally. The attempt then remains UNKNOWN and must not be resent.
The system does not claim provider-side idempotency or exactly-once delivery.

## Delivery errors and privacy

Only this marked control retains structured delivery errors in its private trace:
all supplied standard error fields code/subcode, title, message,
error_data.details/messaging_product and href. It preserves the last failure even
if a later delivery status arrives, plus up to 16 distinct status events. Error
strings are bounded to 8,192 characters, up to 16 errors per callback. Bearer/Meta
tokens, credential assignments, phone-like numbers and free-text URLs are
redacted. href is retained only for HTTPS developers.facebook.com/docs/ URLs with
no query/fragment. Thus evidence is sanitized, not an unredacted webhook archive.
Unknown fields, recipient fields and raw request bodies are not retained/logged.
Normal text, Saved Voices and other experiments keep their existing behavior.

The candidate page separates upload HTTP result, submission HTTP/acceptance,
delivery status/error, and manually recorded phone playback. Full safe error
fields appear in administrator Advanced evidence under delivery_failure and
delivery_events. HTTP 200 and delivered never auto-mark Android playback as plays.

## Deploy and run (no messages are sent by deployment)

1. Review/merge this PR only with approval; deploy the reviewed commit to Railway.
   There is no database migration, new dependency or audio-encoder change.
2. Keep `VOICE_FFMPEG_ENCODER_ENABLED=false` and Global AI OFF. Verify actual web
   instance commit/file hashes against Railway deployment details.
3. Provision the existing verified owner destination; enable existing
   `VOICE_PLAYBACK_TEST_ENABLED=true` and `VOICE_FFMPEG_OWNER_TEST_ENABLED=true`.
   Enable new `VOICE_EXACT_AUDIO_CONTROL_ENABLED=true` only for this comparison.
   Never use a customer number. Ensure pinned optional diagnostics tool is still
   installed for existing structural/independent decode preflight.
4. Owner sends an inbound WhatsApp message to open the service window. Signed-in
   administrator opens `/crm/bot-management/voice-diagnostics/ab?run_id=def27d027c1742be8f4baa8e8e358efb`.
5. At “Prepare exact-byte control — send nothing”, enter the configured owner
   number and confirm owner-only preparation. This copies/checks audio and renders
   the review page; it sends nothing. Verify SHA/size and ordinary payload.
6. Only after owner approval, check the ONE-message authorization and press Send
   One Candidate Message. Do not double-submit or retry unknown outcomes.
7. Refresh the same review page to inspect upload, submission and delivery events.
   On the owner's Android WhatsApp app, download and play the new ordinary audio.
   Record actual phone results separately; export the redacted Advanced evidence.
8. If ordinary audio plays, this narrows failure to native delivery for these
   exact bytes/account/API conditions. It does not prove a universal voice-flag
   defect. If it fails, inspect the detailed error before any further test.

No message has been sent during implementation or automated testing. Owner phone
playback remains unverified until the separate manual action above.

Automated audio/webhook/candidate/report regression checks: **138 passed, 2
skipped**. The exact-byte tests use a synthetic immutable fixture with a test-only
fingerprint override, never the owner's private audio. They verify identity of
copied/uploaded bytes, omitted voice key, one-message authorization, CSRF/admin/
owner gates, expiration/window/AI/encoder gates, duplicate preparation, concurrent
send claims, UNKNOWN fencing, early webhook correlation, detailed sanitized
failure retention, and preservation of the reference during cleanup. Existing
encoder, browser recording, Saved Voices and WhatsApp tests remain included.
Additional Global AI, Quick Messages and report regressions: **57 passed, 2
skipped**. Skipped checks were not counted as passing. All provider calls in these
automated tests were mocked.

## Rollback

Set `VOICE_EXACT_AUDIO_CONTROL_ENABLED=false` on all web instances and restart/
redeploy them. Keep the production encoder false and Global AI OFF. Disabling the
test prevents new boundary crossings but cannot recall an HTTP request already
accepted by Meta. Never retry an uncertain attempt. Retain evidence; cleanup
deletes only the new control's temporary copies. The reference snapshot and
Saved Voices originals remain. If rolling code back, delivery-detail capture for
this control also stops; do not discard existing JSON evidence. No schema rollback
or audio re-encoding is required.
