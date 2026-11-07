# WhatsApp conversation resolution and follow-ups

The Inbox and dashboard both use `inbox_reply_queue.summary()`. Resolution and deferral suppress the same authoritative unanswered-turn query, rather than maintaining another dashboard counter. Existing message delivery, unread state, owner/shared access, lead stages, orders and marketing consent remain independent.

## Staff workflow

The compact conversation controls offer Resolve, Follow up, Reopen, Reschedule, Cancel follow-up and History. Each write opens an explicit confirmation dialog with optional predefined reason and note. Follow-up input is labelled Sri Lanka time and submitted with `+05:30`, independent of browser timezone. It is stored as naive UTC, matching existing database timestamps. A follow-up never sends a message.

Administrators can check individual rows, select currently visible rows (maximum 100), clear selections and resolve exactly the indicated selection. Selection stores the original review snapshot; polling does not silently refresh it. Filter changes clear selection. Successful rows are removed from selection; conflicts remain selected and require clearing/reviewing before a fresh action. The API reports every selected row's outcome and never expands selection to the entire database. Writes lock conversations in ID order and use per-row savepoints within one transaction; SQLite explicitly starts its write transaction before savepoints without changing global session settings.

Read-only and reply-only shares can view history but cannot change workload state; only the current owner or administrator can manage it. Follow-up assignment does not grant conversation access or change the Inbox owner: the assignee must already own or have explicit shared access. Relevant section grants apply to dashboard aggregates, actions, history and filters. All new endpoints are private/no-store and mutations require CSRF.

## State and queue semantics

- `NEEDS_REPLY` enables normal unanswered-turn handling. A manual reopen explicitly requests attention even when a successful reply was recorded previously. A later successful staff/automatic reply returns the chat to the existing Waiting for customer view; it does not invent a manual resolution.
- `FOLLOW_UP_LATER` removes immediate reply attention and exposes the chat in Follow up later. At the due time it also appears in Follow-ups due, without automatically changing state or sending anything.
- `RESOLVED` removes immediate attention, retains all history and remains searchable in All and Resolved. Resolution does not change list ordering in All, unread totals, owner, lead/order stages, customer interests or consent.

The stored NEEDS_REPLY mode is deliberately neutral for inherited conversations. The actual attention flag remains derived from unanswered messages (or explicit reopen), so historical Waiting for customer/no-activity chats are not suddenly queued or automatically labelled resolved. UI labels distinguish Needs reply from No pending reply.

A status action acknowledges the inbound history actually reviewed. On a new inquiry, old resolved/deferred messages do not reappear as the start of the new waiting timer. Failed/unknown outgoing attempts never clear an unanswered turn; later delivery failures retain the existing queue restoration behavior.

The new filters preserve all old URL parameters and add `reply_queue=follow_up_later`, `followups_due`, `resolved` and `window_expired`. Window expiry is a review filter, never automatic resolution. Its test uses the same timestamp/fallback convention as ordinary text sending. Reopening or scheduling does not extend Meta's 24-hour window. Closed windows are labelled and ordinary text submission stays disabled/rejected; separate approved-template messaging is not added.

## Due-work integration

The dashboard's current Follow-ups due is a permission-scoped union of conversation schedules and existing Lead.next_followup dates. It creates no new Lead or duplicate reminder records. A resolved/deferred conversation overrides its linked legacy lead reminder; an immediate reply queue entry is not counted again as a lead follow-up. Historical unlinked leads remain available under existing lead permissions. Conversation follow-ups are accessible with WhatsApp permission even if the user lacks CRM Leads access; restricted lead records and totals remain hidden.

The list is bounded/paginated and prioritizes today's/recent due work over ancient legacy dates. Date-only legacy reminders remain date-only; conversation schedules display an explicit Sri Lanka date and time. Due status is calculated on reads, so no scheduler/worker is required and no automatic outbound work exists.

## Concurrency and inbound processing

State rows have monotonically increasing versions. Review snapshots contain the current version, maximum local inbound identity and a server-signed review time. They expire after 24 hours and are not authorization grants. Mutations compare all three and lock the current conversation, then use a conditional state update. Repeated action keys with the same payload return the existing result; changed payloads conflict. Audit events have unique deduplication keys.

Qualifying registered customer messages (text, voice/audio, image, video, document, sticker and supported interactive replies) update state inside the existing receipt-claim/message transaction. New qualifying inquiries reopen resolved/deferred states and record one system event. Delivery/read callbacks and staff outbound messages do not reopen state. Receipt-only unsupported/reaction events remain nonqualifying; this does not broaden unrelated webhook/consent handling.

Old provider timestamps and duplicate provider IDs do not reopen old inquiries. Meta timestamps have whole-second precision: same-second new identities are conservatively treated as new rather than hidden. The reviewed boundary, rather than the later resolution commit time, protects a customer message arriving while a bulk action is running. The acknowledged inbound ID prevents old history from being counted again. Provider timestamps do not move conversation/customer-window timestamps backwards. This retains existing canonical phone/conversation lookup and receipt idempotency.

There are no message deletions and no mutation of marketing-consent handling. Global AI controls, Saved Voices, Quick Messages, attachment validation and audio encoding retain their existing implementation.

## Schema and migration

Additive revision `20261107_28` follows `20261107_27`:

- `conversation_work_states`: one override per conversation, status, version, updated/actor/reason, resolution time, due time, optional follow-up assignee, inbound watermark, review boundary, acknowledged inbound ID and explicit attention/reply baseline.
- `conversation_work_events`: append-only application audit with previous/new status, actor snapshot, reason, trigger, local inbound ID and follow-up details. Deleted users do not erase retained actor snapshots. There are no edit/delete endpoints; ORM audit edits/deletes are rejected, following the existing consent-audit convention.
- Status values fit VARCHAR(24), reasons VARCHAR(1000), audit trigger VARCHAR(16) and action keys VARCHAR(160). A status CHECK constraint protects valid values. Conversation FKs use RESTRICT to retain history.
- A `(phone_number,direction,created_at,id)` message index supports per-conversation latest-message and unanswered-turn lookups. Work status/due and audit conversation/date indexes support the new bounded queries.

Backfill is a single SQL insert/select for existing conversations, with inbound watermarks and neutral inherited reply handling. It never bulk-resolves historical chats or modifies messages, leads, ownership, consent or AI. Existing partial backfill rows are preserved on repeated upgrade. Downgrade intentionally retains tables, indexes and audit data.

## Deployment and rollback

1. Review the PR; back up the database and verify current head `20261107_27`.
2. In staging, pause writers, apply `alembic upgrade head`, start the new version and validate administrator/owner/shared permissions and dashboard/Inbox parity. Use a representative dataset to inspect query plans/latency, particularly derived reply turns and bounded 100-row status batches.
3. Use the same paused-writer migration/start sequence for an approved production release. No environment variables, Meta configuration or AI changes are required. This implementation task does not run production migrations or deploy.
4. Roll back application behavior by restoring the preceding app version while retaining the additive schema. The prior app uses raw unanswered-turn behavior, so manually resolved chats can become visible again; history remains safe.
5. Avoid mixed-version writers. Before re-enabling resolution after an old-writer rollback period, reconcile qualifying inbound messages received during that period against retained resolved/deferred watermarks using `conversation_work_service.inbound()` in a reviewed maintenance transaction. Retained state must not hide inquiries recorded by an old app that did not know about reopening. This reconciliation is an operational prerequisite, not an automatic production action performed by this task.

## Verification and limits

Native SQLite and PostgreSQL tests cover real pre-28 schema migration, repeated upgrade, retained downgrade/re-upgrade, existing history and Global AI OFF. The PostgreSQL race test holds an uncommitted inbound until after a reviewed manual resolution, then proves the new inquiry wins and duplicate receipt delivery does not add another reopening event. Application tests cover individual/bulk actions, partial conflict, unselected records, all qualifying media kinds, permissions, CSRF, stale/signed reviews, schedules, legacy deduplication, immutable history and unchanged ownership/consent. Chromium tests cover dashboard actions, explicit bulk confirmation, desktop and 320/360/390/430px widths. Fixture screenshots: [desktop](screenshots/conversation-work-desktop.png), [mobile](screenshots/conversation-work-mobile.png).

List/status reads use bounded batch metadata queries. Status writes deliberately perform per-record authorization/CAS/audit work inside the 100-row bound. Production-sized latency has not been measured from this isolated environment. Audit APIs page by 50, and the UI offers older changes. Legacy unsupported receipt-only messages are not reconstructed as new actionable conversations. No live customer messages, production migration, environment change, deployment or merge is performed.

### Final validation results

- 29 new state/migration/concurrency/browser checks passed, including native PostgreSQL and whole-second timestamp ordering.
- Final targeted state + shared Inbox/Dashboard suite: 55 passed. The final AI/queue/state selection also passed (58 passed, two optional PostgreSQL locks cases skipped there and covered separately).
- Broad existing selection: 516 passed, 14 PostgreSQL cases skipped, with one old Quick Message browser assertion failing because the previously merged link renderer normalizes a trailing slash and can produce multiple anchors. The assertion/locator was updated to that existing behavior and the complete affected browser flow then passed. All 517 selected existing cases are therefore verified across those runs.
- All 14 skipped PostgreSQL cases passed separately (the larger PostgreSQL selection had 15 passes including two repeated SQLite variants; the details case passed separately).
- JavaScript syntax, Python compilation and diff whitespace checks passed. Existing UTC/dependency deprecation warnings remain.

Changed components: `models.py`; migration `20261107_28_conversation_resolution.py`; `conversation_work_service.py`, `conversation_work_routes.py`, `conversation_followups.py`; shared `inbox_reply_queue.py`; integration in `main.py` and `crm_dashboard_service.py`; `conversation_work.js/.css`, Inbox/filter/dashboard JavaScript; shared work dialog and Inbox/Dashboard/base templates; three new work test modules. Existing migration-head expectations follow revision 28, the dashboard permission test now expects scoped WhatsApp follow-ups without granting lead totals, and the Quick Message browser test follows the existing normalized-link behavior. Saved Voices/audio implementation files were not modified.
