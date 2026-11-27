# Optional spoken disclosure

The exact language-specific prefix was an application send constraint. This release
removes that constraint and starts the editor empty; it neither strips existing
transcripts nor adds or edits spoken content. Staff can still include a disclosure.
Internal AI labels and explicit listening/recipient approval remain visible.

New clients submit `previewed=true` plus `approved=true`, the audio hash and assignment
version. Original clients with `heard_disclosure=true` remain accepted. Neither missing
nor false preview approval is accepted. Original request envelopes are hashed unchanged
so existing send intents remain recoverable. No schema, environment variable or migration
change. SDK, transport, recipient/window checks, quotas and send uniqueness are unchanged.

## Policy review limitation

On 27 November 2026 this environment could not retrieve the current official pages
https://ai.google.dev/gemini-api/docs/speech-generation and
https://business.whatsapp.com/policy (network CONNECT 403). No claim is made that
current Google, Meta, jurisdictional or contractual disclosure obligations have been
fully verified. Before enabling this behavior in production, the administrator must
review the applicable terms and determine how any required disclosure is provided.
Do not present synthetic audio as an actual recording of a human.

## Deploy and accept

Review and merge separately, deploy the approved commit through the usual process.
No migration or flag change is required; existing send/generation gates still apply.
With a synthetic transcript and mocked or separately authorized test recipient, confirm
empty editor, unchanged text across language selection, successful generation without
a forced prefix, disabled send until approval, editing invalidates approval, exactly
one send, and status recovery without another send. Listen and verify actual delivery
separately. Deployment alone must not generate or send anything.

## Rollback

Disable sending using the existing policy/gate if needed and redeploy the preceding
approved commit. Preserve all jobs, requests, usage and audit. Previous code again
requires its exact prefix for new sends; existing audio without it must not be replayed
or edited in storage. Inspect existing intent status before any separately approved
new send. No database rollback is needed.
