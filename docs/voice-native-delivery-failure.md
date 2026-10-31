# Owner candidate delivery failure: def27d027c1742be8f4baa8e8e358efb

## Evidence and limits

Repository inspected at main d8a5921 (candidate commit 5d59dca merged). No Railway,
production database or Meta account access was available for this investigation.
The owner supplied the exact run's Advanced evidence during this investigation.
It was inspected here, but not independently retrieved from production. A raw
status webhook was not supplied. The supplied trace establishes:

- Encoding, OGG/Opus validation and independent decoding passed.
- Media upload and message submission returned HTTP 200.
- Delivery subsequently failed with error 131053.
- Meta-retrieved bytes match the uploaded SHA-256.
- Native audio payload contained `audio.voice = true`.
- Earlier reference C played as ordinary audio with the voice flag omitted.

Specific correlation from that evidence:

- Runtime artifact/platform commit both `d8a5921506b71f2e7cc92908540f706bd5ba3bcf`.
  Local hashes of main.py, whatsapp_service.py, audio_service.py,
  voice_ffmpeg_encoder.py and voice_diagnostic_tools.py match the supplied runtime.
  This corroborates the supplied provenance; Railway was not directly accessed.
- Prepared 31 October 2026, 10:30:35 Sri Lanka time; authorized 10:30:41.
- Recording 12/revision 1, source AAC/M4A, 386,620 bytes, approximately 10.987s.
- Snapshot, validation, actual multipart and Meta-retrieved bytes all share
  SHA-256 `b2f13701d7e62e9229afa53f9e866e7791073eecbdfedd85d1230607fe5243b2`,
  length 41,771 bytes; Meta metadata also agrees on size/hash and audio/ogg.
- Uploaded media ID `2474116293124365` matches send payload. Upload/send HTTP 200;
  stored SENT is API acceptance while delivery_status is failed, code 131053.
- Candidate and reference C both use Graph v26.0 and the same recorded business
  phone hash. No differing business routing is visible in supplied traces.
- Actual candidate uses the pinned FFmpeg 7.0.2 binary and actual public candidate
  code path. Production encoder flag at preparation was false.
- Reference C's stored Android playback is now recorded as plays; candidate
  playback remains null. No append-only database audit rows were supplied, only
  embedded prepare/authorize/send/read actions.

Additional output differences remain uncontrolled: candidate has nine OpusTags
comments versus reference C's one, 550 versus 529 audio packets, a different source
and duration, and initial audio granules 47,999/95,999 versus C's 48,000/96,000.
Both have the same vendor hash, OpusHead parameters, hybrid mode, 20ms encoded
frames and EOS. No tag content or actual packet bytes were supplied for offline
analysis here. These observations do not prove any tag/granule causes rejection.

This establishes a reported asynchronous delivery failure, distinct from the earlier
delivered-but-unplayable symptom. Successful submission is not successful delivery.
Matching media hashes rules out alteration across the observed upload/retrieval
boundaries; it does not prove Meta's voice-note processing accepts those bytes.

Error 131053 is commonly documented by Meta as **Media upload error**, with guidance
to inspect the error details for the specific cause. That broad label alone does
not establish a native voice restriction, codec defect or recipient issue.
Current official documentation could not be fetched here (network proxy HTTP 403).
Do not represent this label as the actual title received for this message.

Official references to check alongside the original error object's `href`:

- https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes/
- https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/components/
- https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages/
- https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media/

## Confirmed repository finding

`main.py`, status-webhook processing, updates the message's send status and copies
only the first integer `errors[0].code` to SavedVoiceSend and VoicePlaybackVariant.
It discards every error's title, message, error_data.details and href. The manual
browser audio trace also gets only status/code; candidate traces get neither full
error objects nor a history of status events. The handler overwrites the latest
status/code on subsequent callbacks. A webhook HTTP 200 acknowledges receipt only.

Consequently, the existing candidate row cannot supply the complete Meta error.
Check an already-configured webhook capture, log archive or Meta diagnostic view
for this exact message ID and timestamp. Do not invent text from the numeric code.
Share only the redacted `errors` object, removing recipient fields, credentials,
signed URLs and any private data inside free-text details. No broad request-body
logging should be enabled. No status capture behavior was changed in this patch.

## Payload comparison

Both routes use the existing business phone media-upload endpoint, actual validated
OGG/Opus bytes, multipart `audio/ogg`, the returned media ID, and audio submission.
Reference C uses `{"type":"audio","audio":{"id":"<C media>"}}`.
Native candidate uses `{"type":"audio","audio":{"id":"<P media>","voice":true}}`.
The supplied traces confirm these payloads, equal business-phone hashes and Graph
versions for the failed run and reference. Raw recipient routing is not logged.

The candidate and reference C have different source recordings. Candidate encoding
also uses seekable input and a longer duration guard. Thus the voice flag is a
plausible lead, not an isolated cause. No production encoder or payload change is
justified solely by this comparison.

## Read-only collection

An operator with production database access can run:

```sh
python -m voice_delivery_failure_report --run-id def27d027c1742be8f4baa8e8e358efb --owner-authorized
```

This uses PostgreSQL READ ONLY, rolls back, makes no Meta requests, and does not
send, transcode, change settings or output recipient-bearing WhatsApp IDs. It
reports stored status, actual voice flag, upload fingerprint, API results,
payload/media consistency and matching audit event IDs/actions/times. It explicitly
reports missing error text. Existing administrator Advanced evidence can supply
full trace correlation without installing this helper. Neither method recovers
discarded webhook fields. Verify the running commit separately in Railway.

## Smallest safe next action

Keep the production FFmpeg flag false and Global AI OFF. Preserve the failed run,
source, output, traces and original Saved Voice. First collect any existing exact
error object; no sends are needed for this step. Do not retry the failed attempt.

If those details do not establish the cause, propose a separately authorized
owner-only comparison using **the exact failed candidate output bytes**, not a
new recording or another encoding. Send one ordinary-audio control with the voice
flag omitted, a new media upload and a distinct idempotency key. Confirm its hash
matches the failed candidate and retain its complete sanitized status error if it
fails. Existing owner preparation creates new encodings, so it is not by itself
this exact-byte experiment; a reviewed controlled path would be required first.
No such send or send implementation is authorized/performed by this investigation.

Ordinary playback success would narrow the problem to the native delivery path
for this file/account/API combination; it would not prove a universal voice-flag
defect. Ordinary failure would implicate a shared media/delivery factor. Require
actual Android playback, not just delivered status. Any production switch to
ordinary attachments or encoder change requires further authorization.

## Change and validation

Only this report, a standalone read-only collection helper and its tests are
added. No webhook, encoder, WhatsApp payload, settings, schema or UI is changed.
Targeted checks: three new report tests plus the existing mocked delivery-webhook
regression passed (4 passed, 33 deselected). The webhook regression also verifies
Global AI remains OFF and private provider text/recipient data is not exposed.
No real Meta calls, production queries, messages, deployment or merge performed.
