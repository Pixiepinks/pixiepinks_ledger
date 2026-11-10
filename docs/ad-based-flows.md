# Ad-Based Saved Flows

## Existing architecture and integration

WhatsApp inbound messages, processed delivery receipts and message-scoped advertisement referrals are already registered in one transaction in `main._store_conversation_message`. `referral.source_id` is the exact advertisement key; `ctwa_clid` stays in the original private referral and is not required or copied into mappings. Existing referral cards remain intact.

The existing saved library (`CustomerQuestionFlow`), builder (`/crm/quick-messages/customer-questions`), durable run engine (`customer_questions`), button/list routing, product/service/delivery/payment steps and startup worker are reused. This feature creates ordinary `CustomerQuestionRun` records through `customer_questions.start`; it does not create a second flow engine, transport or scheduler.

Existing flows have saved/archived states, not a separate draft/publication flag. Here an eligible “published” flow means a saved, validated, non-archived flow version that an administrator explicitly approves by saving/enabling its ad mapping. Each mapping pins the saved version. Editing the builder subsequently requires reviewing/saving that mapping again before another automatic start. Already-running flows retain their original immutable definition.

Ad-to-staff routing runs first. Existing owners remain assigned. A flow mapping blocks assigned conversations by default; an administrator can explicitly choose **Allow on assigned chats**, which preserves the owner and allows only AI-mode conversations. HUMAN takeover always blocks/ends mapped automation. Ads metadata synchronization is a separate read-only cache; neither its enabled variable nor the presence of a token is treated as proof it works. Triggering uses stored referrals and works with Ads sync absent or OFF.

## Frontend

Administrators open **Automation → Ad-Based Flows** (`/crm/ad-flows`).

- Master enable/disable (defaults OFF), with the existing saved-engine and webhook-signature states shown separately.
- Create Mapping / Edit, enabled status, Enable/Disable and Delete actions.
- Searchable mapping list and advertisement chooser populated from retained referrals, cached Ads metadata and manually mapped IDs. Numeric Ad IDs may be entered manually; names are display labels only. IDs remain strings, including in JavaScript.
- Eligible saved-flow selector, current version, default policy A, repeat cooldown and explicit assigned-chat allowance.
- Cached campaign/ad-set labels and authenticated referral thumbnail where available; unavailable metadata does not block mapping.
- Queued starts, transport-accepted starts, failed/uncertain initial starts, skipped attempts, last successful trigger time and current/latest execution status. A queued start is a durable engine run; an accepted start has at least one retained SENT event/product send. Neither means WhatsApp delivered or read every subsequent message. Failures after an accepted first step remain visible in execution status/history rather than being counted as a failed initial start.
- Test rule: checks a simulated new customer, or an optional existing conversation ID. It creates no execution and sends no message. It cannot certify provider acceptance or future stock/media availability.
- History: up to 100 recent execution outcomes and mapping audit events. Link each attempt to the existing customer conversation. Failures use fixed safe reasons, never raw provider payloads or credentials.

One retained mapping exists per exact Ad ID. This is the conflict/priority policy; two enabled mappings cannot compete for one advertisement. Delete is a soft delete: disable the mapping, pause its active runs and preserve execution/audit evidence. Creating that Ad ID again restores the retained mapping with a new version; existing non-deleted edits require the current version.

## Trigger policies and lifecycle

**Policy A — FIRST (default):** the ad referral must be on the first accepted inbound message that creates a new `WhatsAppConversation` record. A customer who already has a CRM thread, including an older organic enquiry or a resolved/reopened thread, is not new. WhatsApp billing windows and the 24-hour reply window are not used to invent a new CRM thread.

**Policy B — REPEAT:** may start on a new ad-bearing inbound message in an existing thread only when no nonterminal saved run exists and the configured cooldown has expired. Cooldown is 1–8760 hours (default 24), measured since the most recent successful ad-flow start across all ad mappings for that conversation. Re-entering another advertisement cannot bypass the cooldown. PAUSED, SENDING, UNCERTAIN and WINDOW_CLOSED runs still require review/Stop; they are not silently replaced.

Both policies require:

1. A verified inbound webhook with actual `source_type=ad` and a valid numeric `source_id` matching the exact mapping. Text similarity, creative names, product categories and ordinary organic messages never select an ad flow.
2. The ad-flow master and existing Customer Question Automation master are both ON. Enabling one does not silently enable the other.
3. An enabled, non-deleted mapping, approved saved version, valid flow/resources and an available configuring administrator. Unsupported step types, including invented AI steps, are rejected rather than executed through a hidden generative path.
4. No active saved run or existing product discovery workflow, no customer opt-out, no HUMAN takeover, and no assignment unless explicitly permitted by the mapping.
5. The existing WhatsApp customer-service reply window is open. This feature never creates templates or sends outside that window; the normal engine rechecks it before every send.

Older out-of-order ad messages cannot supersede newer saved referral evidence. The original receipt/message/referral remains stored whether a mapped start is queued, skipped or rejected. Missing/archived/edited flows produce a safe failed-start history entry rather than losing the customer enquiry. Invalid/no-match source IDs do not create a guessed mapping.

## Concurrency and controls

The existing question configuration lock is acquired before conversation mutation. It serializes starts/administrative changes and serves as the send barrier. Each mapped execution has a database-unique originating message ID and a unique linked run. Existing provider receipt uniqueness handles webhook redelivery, while conversation/run locks prevent simultaneous different enquiries from creating competing runs. All receipt, referral, run, execution and start audit writes commit atomically. Database failure preserves the existing retryable ingestion behavior, rather than acknowledging an unrecorded start.

Actual sends are left to the existing worker's claim-before-HTTP behavior. It does not automatically replay uncertain provider sends. Ad controls are checked before claim, send and advancement. Master OFF and rule Disable/Delete pause mapped runs under the same configuration barrier; they wait for an already-entered send to finish before returning and block subsequent sends. They do not pause unrelated manually started flows or alter Global AI.

Mapped active/paused/uncertain runs suppress competing generative/legacy automatic replies. The original ad-triggering receipt is not admitted for a competing AI response. Already admitted AI jobs are rechecked at model checkpoints and the final provider send boundary, with a shared question-control lock. A HUMAN takeover also blocks queued replies in conversations with mapped-flow history. The global AI row, generation and configuration are never enabled or rewritten by this feature. Explicit manual sends remain independent of that generative-job context.

The normal Inbox shows source advertisement, assigned flow title, run state, start timestamp, current step and Pause Flow / Stop Flow for the owner or administrator. Its Take over action uses the existing HUMAN takeover endpoint and stops the mapped run. The existing Customer Questions dialog remains the interface for approved Resume and full run/answer history. Resume rechecks ad controls; disabling a mapping/master cannot be bypassed by resuming from that dialog.

## Opt-outs and consent

A verified plain-text customer STOP/CANCEL/unsubscribe (including the listed Sinhala/Tamil stop phrases) stops mapped execution and persists a conversation-level ad-flow opt-out. Control words in interactive button titles are not trusted as standalone STOP commands; normal button IDs/context still drive the engine. These commands are not captured as delivery-detail answers.

Future ad flows remain blocked until that customer sends the explicit signed inbound command **START AD FLOWS**. Staff cannot clear the opt-out through a configuration endpoint. This command only clears the ad-flow preference; it does not start a flow, bypass cooldown or create marketing consent. A later eligible ad enquiry (or an explicit normal staff-assisted workflow) is still required. Existing marketing opt-out handling runs as before; no ad mapping grants or fabricates promotional permission.

## API changes

All configuration endpoints require an authenticated administrator and all writes require existing CSRF validation. Responses use private/no-store. Staff with analytics or quick-message grants do not thereby receive mapping-administration rights.

| Method / route | Purpose |
| --- | --- |
| GET `/crm/ad-flows` | Administration screen |
| GET `/crm/ad-flows/api?q=&offset=` | Controls, mappings, cached ads and eligible saved versions; 50 mappings/page |
| POST `/crm/ad-flows/control` | Set master with optimistic version |
| PUT `/crm/ad-flows/rules/{ad_id}` | Create/edit approved mapping version |
| POST `/crm/ad-flows/rules/{ad_id}/toggle` | Enable/disable with version |
| POST `/crm/ad-flows/rules/{ad_id}/delete` | Soft delete/pause with version |
| POST `/crm/ad-flows/rules/{ad_id}/test` | Read-only dry run; optional conversation ID |
| GET `/crm/ad-flows/history/{ad_id}` | Execution and configuration audit history |

The existing per-conversation Customer Questions API gains additive `ad_automation` information. Existing owner/admin Pause/Stop/Resume actions and takeover are reused. No existing endpoint or payload is removed.

## Migration and deployment

Revision **20261110_38**, after **20261109_37**, adds:

- `ad_flow_control` (seeded OFF, version 0).
- `ad_flow_rules` (unique Ad ID and pinned saved-flow identity/version).
- `ad_flow_executions` (unique originating message and linked normal run, safe attempt outcome).
- `ad_flow_audits` (administrator mapping/control evidence).
- `whatsapp_conversations.ad_flow_opted_out` (default false).

Apply `alembic upgrade head` in the normal reviewed deployment process before updated app code. The migration is repeat-safe; it does not create mappings, start runs, modify saved definitions or alter any Global AI/consent/ownership value. The existing application startup question worker executes queued steps; no new cron/service/subscription is added.

No new environment variables are needed. Existing `META_APP_SECRET` signature verification and the existing WhatsApp transport credentials must already work. Meta Ads tokens and synchronization are not required. Keep Global AI in its intended state. An administrator must explicitly enable the saved-flow master and the new ad master, create/approve a mapping, run the safe dry run and then test a real customer-initiated message through the usual reviewed rollout. Local tests do not certify live Meta delivery.

Rollback: turn the ad master OFF first (pauses mapped runs), then Stop any mapped runs that must not resume using the existing Inbox controls before redeploying the previous application. Previous code does not understand ad-specific guards, so leaving queued mapped runs would let its ordinary question worker send them. Retain additive tables/columns and original flow snapshots/history; downgrade deliberately preserves them. No code in this task changes production controls, merges, deploys or sends live messages.

## Files and test scope

New foundation: `ad_flow_models.py`, `ad_flow_service.py`, `ad_flow_routes.py`, `alembic/versions/20261110_38_ad_flows.py`.

Integration: `models.py`, `main.py`, `customer_questions.py`, `customer_question_routes.py`, `global_ai_control.py`, `staff_access.py`, `templates/base.html`, `templates/whatsapp_inbox.html`.

Frontend: `templates/ad_flows.html`, `static/ad_flows.js`, `static/inbox_ad_flow.js`.

Tests/docs: `tests/test_ad_flows.py`, `tests/test_ad_flows_ui.py`, this document; the existing global-AI migration assertion now follows Alembic's current head rather than hard-coding the obsolete revision 20261108_34, retaining all receipt and switch-preservation checks.

Provider transports are mocked in isolated SQLite tests and Chromium is used for representative frontend/Inbox interactions. A signed webhook → mapped normal run → first question → customer answer → next question → completion test passed with Global AI OFF and without ctwa_clid. Duplicate/concurrent receipts and repeat enquiries, owner/takeover protection, opt-outs, unavailable/changed flows, dry runs, migration and guarded AI sends are covered. PostgreSQL-only existing tests are reported as skipped if their configured server is unavailable; no live end-to-end delivery claim is made.

### Recorded verification

- **30 new ad-flow tests passed**, including representative signed webhook-to-worker completion with mocked sends, both trigger policies, concurrent same/different deliveries, no ctwa_clid, organic/text-similarity exclusion, missing/invalid IDs, unavailable/edited flows, customer opt-out/opt-in, untrusted button-title handling, queue/acceptance/failure counters, takeover/master/delete guards, already-admitted AI send suppression, safe dry run, migration and desktop/390px/320px UI.
- **36 existing question-engine tests passed.**
- **66 existing Global AI, flow-library, Inbox UI and ad-assignment tests passed; 2 PostgreSQL-only checks skipped** because their real-server test configuration was unavailable.
- **91 existing product, greasing voice, delivery-detail, payment and shared-route authorization checks passed.**
- Total: **223 passed, 2 skipped** (unique tests; focused positive-acceptance recheck also passed). Python/JavaScript syntax and whitespace checks passed.
- These are local, isolated tests with mocked provider sends, not live Meta delivery validation. No production flags, messages or data were changed.

The reviewed implementation is delivered on `feature/ad-saved-flow-automation` for a pull request to `main`. Merging, deployment and activation remain separate actions.

## Final review (10 November 2026)

The review corrected a recovery hazard: disabling a mapping/master must not turn a persisted `SENDING` or `UNCERTAIN` outcome into a resumable pause. Such sends now remain `UNCERTAIN` with `UNREVIEWED_SEND`; a closed-window run remains closed. Only the worker holding its own known-unsent claim may safely release that claim before entering the transport. This prevents control cycling from causing duplicate delivery after a crash or ambiguous provider outcome.

### Production activation checklist

1. Review and merge separately; deploy the additive migration through the normal process.
2. Verify existing conversations, saved flows, ownership, consent and Global AI configuration. Keep ad automation OFF during verification.
3. Approve a saved-flow version and exact numeric Ad ID mapping; check FIRST/REPEAT policy, cooldown and assigned-chat handling.
4. Use the read-only dry run with representative conversations. Check opt-out, ownership and reply-window exclusions.
5. An authorized administrator can explicitly enable both required automation controls and the approved rule for a limited customer-initiated test. This review did not perform that activation or send live messages.
6. Monitor execution history and accepted/failed/uncertain outcomes before extending the rollout.

### Monitoring and troubleshooting

Use mapping history for trigger decisions and configuration audits, and Inbox run history for step/send outcomes. Check both automation masters, the rule, pinned flow availability, exact referral Ad ID, active runs, customer cooldown, ownership, opt-out and the 24-hour window when no question starts. Meta Ads Analytics synchronization is not a prerequisite.

A stopped run requires a deliberate new eligible interaction; webhook retries cannot restart it. Re-enabling a master does not resume paused runs. For `UNREVIEWED_SEND`, inspect the customer chat and transport evidence before any manual recovery; do not toggle controls to retry. A process restart does not replay a persisted send claim. Dry runs and local mocked sends do not prove live provider delivery.

### Updated validation

- Broad relevant regression sweep: **905 passed, 35 failed, 22 skipped** across 49 files. All **35 failures reproduce on unchanged main (`947f8b8`)**, including legacy migration expectations and referral/attachment access fixtures. The full suite is therefore not green; these are baseline failures, not introduced by this feature.
- After the recovery correction: **40 feature/review/UI tests passed**, including restart, transaction rollback, ambiguous transport outcomes, in-flight takeover/disable, cross-ad cooldown and control-cycle recovery. Desktop and narrow mobile layouts are covered; the mapping form now uses compact responsive columns.
- A disposable PostgreSQL 16 database passed the **two previously skipped Global AI concurrency tests**, plus **two new migration/concurrent signed-webhook tests**. The new PostgreSQL tests require `AD_FLOW_TEST_DATABASE_URL` pointing to a disposable loopback `pixiepinks_test` database and isolate their tables in temporary schemas. No production database was used.
- Added review suites: `tests/test_ad_flows_review.py` and `tests/test_ad_flows_postgres_review.py`. Provider calls remain mocked; production settings and Global AI were unchanged.

- Post-correction shared-worker regression: **136 passed, 2 skipped** across core questions, selected products, delivery details, payment follow-up/review and Global AI. The two PostgreSQL-only skips in this SQLite run passed separately against disposable PostgreSQL as recorded above.
