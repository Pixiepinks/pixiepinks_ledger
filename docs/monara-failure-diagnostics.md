# Monara failed generation — safe diagnostics and recovery boundaries

## Confirmed production evidence

The owner reported one authorized generation ending FAILED with
`PROVIDER_OR_ENCODING_FAILED_NO_RETRY`. The SDK/custom-voice capability and pinned
FFmpeg checks had passed. This does **not** establish that the API key, model/voice
access, provider response format or complete conversion path succeeded.

The deployed worker deliberately discarded the underlying exception, the parent
collapsed subprocess failures, and the service retained only the generic failure.
Consequently this historical job's exact cause cannot be reconstructed from that
record. Missing AI disclosure is not an explanation: disclosure enforcement occurs
on sending, not generation. Do not assume a billing, permission or encoding cause.

A local client-constructor probe succeeded under the same resource limits with a
synthetic key and networking denied. It performed zero provider requests and is not
production API authentication evidence.

## Immediate operator actions (no additional generation approved)

- Set the generation gate back to `MONARA_TTS_ENABLED=false` through the already
  authorized test shutdown process, and uncheck Allow generation in its policy.
- Keep sending disabled and Global AI OFF. Do not click Generate/Regenerate again.
- Preserve the failed job, immutable audit and reserved usage. Do not reset budgets,
  delete rows or make a new request to bypass the one-attempt limit.
- Run `python -m scripts.validate_monara_readonly` after shutdown if needed. Its
  `provider_calls: 0` concerns the diagnostic itself, not the earlier billable attempt.
- Google AI Studio's existing Logs/usage views may contain evidence for the failed
  request. Review the matching time without issuing a request. Share only numeric
  HTTP status and a sanitized standard reason code, never tokens, transcripts, audio
  or complete request/response bodies. Missing logs do not prove the call was not made.

## Diagnostic-only implementation

`monara_diagnostics.py` strictly allowlists stage, exception class, controlled reason,
numeric HTTP status (when available), and child exit code. Unknown values map to
safe generic values. It never uses raw exception messages, response bodies, URLs,
request data, credentials or arbitrary class names.

The isolated worker identifies SDK import/configuration, request validation,
client setup, provider request/stream and response validation stages. The parent
preserves its sanitized error envelope and identifies process launch/result,
encoder setup, MP3/OGG encoding and validation failures. Service-level output/storage
failures retain safe metadata in the existing `usage.diagnostic` JSON and immutable
GENERATION_FAILED event. The existing status and failure code remain compatible.
The existing authorized job-status API/UI displays the sanitized details.

No migration, dependency/feature flag, model/voice/style/language, PCM/MIME assumption,
quota, admission, timeout, retry, media-sharing or sending behavior is changed. No
existing FAILED job is processed or backfilled. The failed attempt remains fenced.
Only a future *separately approved* generation can produce provider-stage evidence.
A negative exit code identifies termination, not by itself its cause (such as OOM).

## Review/deployment/rollback

Review the draft PR before any merge or deployment. Deploy with generation and
sending disabled; retain migration `20261127_62` and all usage/audit records. The
build's credential-free capability check and production read-only verifier remain
safe. Redeployment cannot recover discarded error details from the old job.

Before another live generation, review existing provider logs if available, determine
whether the original attempt may have been charged, and obtain new explicit owner
approval for any billable test. No such attempt is authorized or executed here.

Rollback redeploys the previously accepted code with both gates disabled and revision
62 retained. New additive JSON metadata can remain; old code ignores it. No database
reset, replay or record deletion is needed.

Tests use mocked provider errors/statuses, malformed output, timeouts, killed-process
exit codes, an offline SDK worker rejection, redaction, persisted audit and no-retry
checks, plus existing Monara/PostgreSQL/browser cases. No live provider or WhatsApp
request was made by these tests.
