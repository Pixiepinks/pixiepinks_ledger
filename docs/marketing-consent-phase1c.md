# Phase 1C — Explicit WhatsApp consent buttons

The consent modal now defaults to **Buttons — tap to agree or decline** and Sinhala. Staff still preview the exact localized body and both English button labels, confirm, and explicitly send. English and Tamil bodies are available. The text/WhatsApp Reply method remains selectable and the API's omitted `format` retains its legacy text default for compatibility. No campaign or automatic consent-request sender exists.

## Evidence and processing

New button requests use wording version `pixiepinks-wa-marketing-v3-buttons-{language}`. Each preparation generates a cryptographically random 192-bit token. Both button IDs, exact labels, body/version and request reference are retained in an append-only CONSENT_BUTTONS_PREPARED event. It uses the existing audit reason JSON field and evidence fields; no migration, model change or backfill is required. Current Alembic head stays 20261106_25. Reopening a preview reuses the stored configuration. Previous text requests and wording are not rewritten.

A single explicit Cloud API `interactive`/`button` message uses the existing authenticated Inbox send endpoint, conversation ownership, CSRF, 24-hour service window, idempotency claim and request lifecycle. Bodies fit the 1024-character interactive body limit, two reply buttons fit the three-button limit, and labels fit the 20-character limit. Provider failure or an absent message ID remains uncertain; no automatic retry or fallback text send occurs. Do not use this feature outside the customer-service window; approved template sending is not included.

Button sending and processing require existing **META_APP_SECRET** configuration. The webhook must pass its existing X-Hub-Signature-256 verification. Missing configuration blocks button preparation/sending with a specific error and button callback processing with a retryable error. Ordinary legacy text workflows remain unchanged. No production environment variables were read out or changed during development.

The new webhook branch persists button title, provider message ID, reply context and receipt in the same transaction as the consent event. It compares the full random button ID and exact retained label, request/recipient identity, outbound provider context and timestamps. A matching title alone is never permission. Agreement requires the active SENT/PENDING request within seven days, no failed delivery, valid canonical identity and no withdrawal history. It reuses the authoritative status, scope and eligibility proof format. Requests become ANSWERED after a valid affirmative click.

A legitimate decline linked to its original request suppresses marketing, including after the request expires or consent was granted. Old affirmative buttons cannot approve a newer request or override an opt-out. Invalid, cross-recipient and stale responses are retained as rejected audit events without granting permission. Valid current tokens with missing/wrong reply context or invalid identity remain pending for administrator review. Existing text YES matching and STOP handling remain available.

No automatic AI/customer reply is enqueued for consent-button callbacks, even if the isolated test fixture turns AI on. Global AI settings are untouched. The existing Saved Voices, Quick Messages, staff attachments, product sending and audio encoding are unchanged.

Meta timestamps have whole-second precision. For button callbacks only, the start-time comparison uses that precision; matching signed provider context establishes causal linkage to the actual sent request. Original timestamps are retained. If a fast callback arrives before the outgoing provider ID is committed, receipt processing rolls back with HTTP 503 so Meta can retry the same ID. This is a receipt retry, never an outgoing send retry.

## Permissions and compatibility

The existing ownership/section policies and administrator-only evidence verification remain in force. Random IDs are returned only through scoped authenticated consent APIs. Audit references contain no credentials. No independently writable consent eligibility flag or new state enum was introduced.

## Deployment and rollback

Review/merge/deploy separately. Deploy application code and versioned v3 static URLs together. No database migration or production configuration modification is part of this change. Before separately approved use, the operator must confirm the existing Meta webhook secret and signature verification are configured and the account accepts session interactive messages. Provider acceptance and physical-phone rendering were not live-tested. A staging/owner-only live smoke test requires separate authorization.

Rollback app/assets to Phase 1B while retaining all audit evidence. Outstanding button callbacks will no longer be understood by the old code and must be reviewed conservatively; they must not be converted to permission. Legitimate decisions already recorded remain authoritative. Do not delete events, suppressions or request records. No automatic merge or deployment was performed.

## Verification

Tests use isolated databases, signed fixture webhooks, mocked Cloud API transport and Chromium. Coverage includes locale previews, exact payload/labels, explicit send, idempotency, wrong tokens/titles/recipients/context, missing signature/configuration, identity conflicts, expiry, replay against newer requests, opt-out during sending, precision/race handling and absence of automatic replies. Desktop/mobile previews and existing text review remain covered. Final suite totals are recorded in the PR.

Final results: 77 consent/backend/Chromium UI/SQLite migration checks passed, 1 optional PostgreSQL migration check skipped; 205 CRM checks passed, 2 optional PostgreSQL-lock checks skipped; 95 Saved Voices/audio/media checks passed, 4 optional PostgreSQL checks skipped. The final screenshot-specific browser check also passed. JavaScript syntax and Git whitespace checks passed. Browser coverage includes desktop and widths 320/360/390/430. Screenshots are `/tmp/phase1c-desktop.png` and `/tmp/phase1c-mobile.png`. Existing dependency deprecation warnings remain. All final checks passed after correcting an accidental product-path edit caught by regression testing; the product sender has no final diff.
