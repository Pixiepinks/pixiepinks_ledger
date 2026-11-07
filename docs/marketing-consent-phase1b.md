# Phase 1B — Marketing consent usability

## Design and policy

Phase 1 recognized YES after normalization but required an exact WhatsApp Reply reference for automatic permission. Unlinked affirmative responses correctly remained pending. This phase keeps that policy and the read-only eligibility function unchanged. A defensive guard now rejects a malformed request with no outbound provider ID instead of treating two missing IDs as a match.

Sinhala is the default in the UI and when the request API omits language. The supplied Sinhala wording is used verbatim. English and Tamil explain the Reply action, YES and STOP. Only new preparations use `pixiepinks-wa-marketing-v2-{language}`. Existing requests, including prepared previews, retain their exact stored text and versions. No message is sent without the existing explicit preview/confirmation and transport flow.

New RESPONSE_REVIEW_REQUIRED events store a small versioned JSON object in the existing audit `reason` field: code, request ID and local message ID. Content remains in existing message/request/evidence fields. Codes distinguish missing context, wrong context, missing request reference, identity failure and other failures. No schema migration is needed. Old events are never modified; they show “Review required — exact reason not recorded.” Their missing request association is not guessed from whichever request happens to be current.

## Review workflow

The existing consent modal adds a compact responsive Review Customer Response card with identity, exact request/response, timestamps, provider IDs, reply reference and evidence reference. Approval retains Phase 1's administrator-only verification permission; ordinary staff do not gain access to private evidence or a new verification role. Staff see the scoped pending/review indicators and can escalate to an administrator. Existing assignments and section access still govern visibility.

An administrator can approve only an unresolved, current review with affirmative retained evidence, matching identity, request wording/version, phone and timestamp consistency, no contradictory reply context and no withdrawal history. Approval requires explicit confirmation and a reason. It delegates to the existing `apply_action(... verify ...)` service; no parallel grant implementation exists. Missing historical proof or a reply to another message blocks this guided approval. Independently verified evidence can still use the pre-existing administrator evidence workflow, without inventing historical linkage.

Keep pending records an append-only decision and leaves the review unresolved. Decline requires an explicit retained decline/withdrawal phrase. Decisions retain reviewed event/message references, actor, evidence and version checks. Identical review submissions are idempotent. No action sends a message. Automatic interpretation has not been expanded and pending contacts remain ineligible.

The dashboard adds a scoped unresolved-review count and Needs Staff Review filter. PENDING derives either Awaiting customer or Staff review required; authoritative database statuses are unchanged. Inbox details show the same indicator and open the existing modal.

## Phase 1C feasibility: interactive reply buttons (investigation only)

The existing `whatsapp_service.send_whatsapp_text` posts `type: text` to the Cloud API messages endpoint. It has no interactive consent sender. The webhook currently handles interactive messages as unsupported rather than extracting consent button responses. No interactive transport or parser was added here.

WhatsApp Cloud API generally supports session interactive reply buttons within the open 24-hour customer-service window without creating an approved message template. Business-initiated messaging outside that window requires the appropriate approved template and applicable permission/policy requirements; verify current Meta rules and account/API-version support before implementation. A “Yes, I agree” label fits the usual reply-button label limit.

A safe future implementation would persist the exact body and button label/version, bind an opaque server-validated button ID to a unique request and recipient, retain outbound provider ID, validate webhook message context plus token, timestamp, active request and current identity, and reuse existing opt-out/idempotency/audit/eligibility controls. A button title alone is not evidence. It needs explicit review/confirmation in the staff sender and tests for forged, stale, replayed and cross-recipient responses. Recommend Phase 1C after separate authorization; no live feasibility messages were sent. Current Meta documentation/account acceptance was not tested against production.

## Deployment and rollback

No migration or environment-variable change is required; Alembic head remains 20261106_25. Review and merge separately, then deploy application/static assets together through the usual approved process. Versioned static URLs prevent stale clients from missing the new UI. Do not change Global AI. Smoke-test with mocked transport in staging, confirm section/assignment access and inspect old/new wording separately. Existing production consent data is not updated on deployment.

Rollback the application/static assets to Phase 1. New JSON reason strings remain valid append-only audit text, and v2 request wording remains stored. Do not downgrade/drop the consent tables or rewrite events. Explicit administrator decisions made after deployment remain real audit decisions and must not be reversed automatically.

## Verification

Tests use isolated databases, Chromium and mocked provider sends. Coverage includes case/whitespace normalization, linked/unlinked/wrong-context replies, malformed request references, identity conflicts, evidence tampering, explicit confirmation, authorization/CSRF, idempotency, immutable history, old wording, derived review filters and mobile/desktop controls. The PR records final suite totals. No production data, messages, AI flags or audio implementation were changed.

Final verification: 53 consent/backend/Chromium UI/SQLite migration checks passed, 1 optional PostgreSQL migration check skipped; 205 CRM checks passed, 2 optional PostgreSQL-lock checks skipped; 95 Saved Voices/audio/media checks passed, 4 optional PostgreSQL checks skipped. JavaScript syntax (`node --check`) and `git diff --check` passed. Browser coverage exercised desktop 1366×900 and mobile widths 320/360/390/430, including explicit confirmation and audited approval. Screenshots were captured to `/tmp/phase1b-desktop.png` and `/tmp/phase1b-mobile.png`. Existing dependency deprecation warnings remain; no failed final checks.
