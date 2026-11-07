# Marketing Consent — Phase 1

This phase records permission for PixiePinks WhatsApp promotions. It does not send campaigns, import bulk opt-ins, change AI controls, or modify audio processing.

## Identity and consent

`marketing_consents` is a separate register linked to existing Customers, Leads and WhatsApp conversations. Normalized E.164 numbers share one identity; missing numbers retain source-specific identities. Registration never creates ordinary customer records. Existing contacts start **NOT_REQUESTED**, including purchasers and advertising leads. Product interests are independent of permission.

States are NOT_REQUESTED, PENDING, OPTED_IN and OPTED_OUT. A phone change never transfers consent. Conflicting canonical customer/conversation links quarantine the identity and require administrator reconciliation. Adding a canonical link to an existing permission requires re-verification. Historical identities remain retained; administrators can include archived identities in the dashboard.

Verified consent needs affirmative evidence, the fixed promotions scope, source, source description, wording and version, reference, evidence date and verifier. Evidence references are retained, never fetched by this feature. Corrections append audit events rather than rewriting history. Database triggers and ORM guards reject event updates/deletes. The audit stores original inbound evidence text, actor and identity snapshots.

Opt-out wins over pending sends and repeated webhooks. Imports cannot overwrite withdrawal. A subsequent opt-in requires fresh customer-request evidence dated after the withdrawal. Invalidating evidence does not erase suppression. Ordinary customer support replies remain independent of marketing eligibility.

## Staff workflow and permissions

Administrators grant the new **Marketing Consent** section through Staff & Access. Dashboard visibility is scoped to assigned/shared conversations for staff; administrators see all contacts. Inbox actions inherit existing conversation access. Mutations require CSRF and idempotency/version checks. Detailed evidence is administrator-only. Staff can manage interests and record withdrawal; administrators verify/correct evidence, invalidate permission and reconcile identities.

Inbox Customer & Order details show compact consent status and a modal. Mobile users open it from Details → Marketing consent. The dashboard provides status KPIs, search, source/status/interest/date filters and 50-record server-side pagination. Dates use Sri Lanka calendar boundaries. Empty/error states and history pagination are included.

## Explicit request and inbound handling

A request is a staff-triggered ordinary support message using the existing authenticated Inbox text sender and its server-side 24-hour reply window. Previewing never sends. Staff choose the supplied English or Sinhala wording, review the exact preview, tick confirmation and explicitly send. Accepted provider IDs mark PENDING; provider delivery is recorded separately. Unknown outcomes block further requests pending administrator review, with no automatic retry.

Defaults: prepared previews expire after 30 minutes; at most 10 previews per staff member/hour and one accepted request per contact/day. A linked affirmative reply must reference the active request and arrive within seven days. Exact normalized English/Sinhala/Tamil affirmative phrases are deterministic; generic unrelated YES never grants permission. STOP, UNSUBSCRIBE and supported clear withdrawal phrases apply independently of request context. Ambiguous linked replies require review. Request/evidence/webhook state changes are transactional and deduplicated.

The administrator verifier role, seven-day affirmative window, preview expiry and conservative identity re-verification are Phase 1 policy defaults for owner review.

## Future eligibility and import boundary

`get_marketing_eligibility(db, customer_id, channel='whatsapp')` is read-only. It rereads current identity and consent, checks suppression, scope, evidence, ambiguity, and matching immutable grant/verification/correction events. Automatic reply evidence must match a retained request without failed delivery. Cached OPTED_IN alone is insufficient.

A future import should require administrator authorization, dry-run validation, canonical-number deduplication, proof of affirmative consent with wording/version/scope/timestamp/source, explicit operator confirmation, row-level audit and an immutable import reference. Reject unsupported claims and never override existing opt-outs. No bulk import or marketing transport endpoint exists in this phase. Owners must define evidence retention and review applicable privacy obligations before campaign use.

## Migration and rollback

Revision **20261106_25**, parent **20261103_24**, freezes the three new table definitions. It backfills existing identities in bounded 200-record batches, always NOT_REQUESTED for new register entries. Existing customer, conversation, lead and user fields are preserved. SQLite and PostgreSQL have append-only database triggers.

Before a separately approved deployment: back up the database, stop old application replicas, run the normal Alembic upgrade, verify head 20261106_25, then start the matching application and grant section access. No production migration was performed during implementation. No environment flags need changing.

Downgrade deliberately retains all consent tables, requests, indexes, suppressions and audit triggers. Older application code ignores the additive tables. Re-upgrade preserves withdrawals and history; this is a retention rollback, not a destructive schema reversal.

## Verification

The resumed run verified upgrade, concurrency, downgrade and re-upgrade on isolated SQLite and temporary PostgreSQL databases. Browser tests use local Chromium and mocked message transport. No live WhatsApp messages, production settings, databases or AI controls were touched. Saved Voices/audio implementation files remain unchanged. See the pull request for final suite totals and skipped checks.

Resumed verification totals: migration-only 2 passed (SQLite and PostgreSQL); consent/migration/browser UI 35 passed; CRM regression 205 passed, 2 optional PostgreSQL-lock checks skipped; audio/media 99 passed; additional migration/voice diagnostics 57 passed, 3 optional checks skipped. Existing dependency deprecation warnings remain. Global-AI-OFF preservation is covered by regression fixtures; no production connection was used to read or change the live flag.
