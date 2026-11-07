# CRM dashboard and WhatsApp analytics

`/crm` uses actual retained records. Reference-image figures and names are not shipped. The existing navigation stays intact. Local fixture screenshots: [desktop](screenshots/crm-dashboard-desktop.png), [mobile](screenshots/crm-dashboard-mobile.png).

## Definitions and sources

| Measure | Definition / source |
| --- | --- |
| Unique contacts | Distinct normalized numbers with inbound Message or receipt during the period. |
| First recorded contacts | The number's earliest inbound across all retained Message and ProcessedWhatsAppMessage history is in the period. Older unavailable history cannot be reconstructed. |
| Incoming messages | Distinct inbound provider IDs; receipt-only events fill history gaps. Legacy Message rows without IDs count separately and are disclosed. |
| Staff messages sent | Distinct outbound provider IDs with explicit staff send evidence. History wins over duplicate ManualSend/SavedVoiceSend claims. No provider ID means no accepted send. |
| Customers contacted | Distinct normalized recipients of accepted staff sends. Staff row unique recipients overlap and are not additive. |
| Categories | Consent request evidence, Quick Message reference, Saved Voice reference/kind, product kind, recorded voice kind, attachment metadata, then explicit tracking or historical staff text. These mutually exclusive categories reconcile to accepted staff sends. Other preserves missing classification. Each separately accepted collection product counts once; captions are not separate sends. |
| Staff activity | Stable recorded sender ID from new metadata or existing SavedVoiceSend/MediaSend evidence. Historical usernames remain explicitly historical; no backfill guesses account IDs. Current conversation ownership never changes the sender. |
| Delivery | Original message send cohort, latest retained provider callback by timestamp, rank tie-break. Read is included in delivered; accepted remains accepted after delivery failure. Failed attempts without provider acceptance are separate. Unknown delivery is not failure. |
| Current work | Existing Inbox reply-queue summary and ordering; unassigned conversations; due/overdue nonclosed Lead follow-ups; SalesOrder PAYMENT_VERIFICATION and distinct orders with READY_TO_DISPATCH fulfillments when existing section grants and feature flags permit. |
| Pipeline | Leads created during the selected period, grouped by their current existing status. No historical status reconstruction or unsupported monetary values. |

AI and system sends are separate secondary counts. Guided automatic product responses whose trigger cannot be proven are shown as automatic/unknown, never attributed to the current owner. Previews, drafts, collection orchestration parents and uncertain no-ID attempts are excluded from accepted sends. Retry claim rows are deduplicated by provider ID; independently accepted provider messages remain separate.

## Filters and access

Dates use Asia/Colombo and naive UTC database timestamps, Monday weeks, complete previous calendar weeks/months, current calendar periods through now, and rolling 7/30-day windows ending now. Custom end dates are inclusive through an exclusive next-local-midnight boundary. Filters persist in the URL; the display shows timezone, period and last successful refresh.

Staff selection scopes inbound contacts, pipeline and current queues by current ownership **or explicit sharing**. Outgoing activity scopes actual sender IDs. Administrators can compare the team; nonadministrators can select themselves only and see assigned/shared conversation data. Reports reflect current access permissions, not historical access snapshots. Staff comparisons require ADMIN, independently of CRM access. Section restrictions also apply to aggregates and drill-downs; order amounts are not exposed. Nonadmin work links use authorized chats, rather than broad legacy order lists.

Country is numbering region derived from recorded phone identity, not residency. The source filter is **recorded referral evidence** (ad/post/any/none), not guessed first-touch acquisition attribution. Missing referral evidence does not prove organic acquisition. Source evidence applies to the raw authorized conversation identity. Invalid numbers remain message records but are excluded from unique-number counts; their coverage count is visible.

Private API and shell responses use `Cache-Control: private, no-store`. SQL parameters enforce filters; no client-only authorization. Matching activity and queue drill-downs use the same scoped queries with 50-row pages. Team rows also page by 50. Current queue previews contain at most five records and expose full matching work through pagination.

## Tracking and migration

Revision `20261107_26` follows `20261106_25`. It adds nullable actor ID/kind/category snapshots to Message, nullable actor ID to ManualSend, a raw-to-canonical phone lookup and a latest-delivery reporting lookup. String widths are 16/24 for actor kind/category, 18 for E.164 identity, 2 for region and 16 for delivery status; existing response_kind stays at 20. Actor snapshots intentionally have no user FK so deleted staff do not erase attribution.

Migration backfills distinct raw phones from retained messages, receipts, conversations and leads in streamed batches of 200; it retains existing message delivery snapshots without inventing callback history. It does not backfill guessed actor IDs or change customers, consent, assignments, AI controls or media. Existing actor indices support future filters; direction/created_at/id and receipt received_at/sender_phone indices support period reads. New callbacks retain event chronology without altering existing operational status logic.

Local mapper hooks update reporting metadata inside Core savepoints in the existing transaction. Reporting metadata failures emit a generic warning and do not block the operational write. Global autoflush/session configuration is unchanged. Missing phone lookup metadata makes analytics return unavailable rather than trustworthy-looking zeroes. Operational callbacks continue if their reporting lookup fails; resolve warnings promptly because delivery analytics can then lag.

## Coverage limits

- Existing text histories may have only usernames; such rows remain Historical/Unattributed rather than guessed stable IDs.
- Accepted claim-only sends lacking a history row use retained attempt creation time, visibly counted in coverage; precise provider acceptance time was not retained for these legacy records.
- Legacy inbound rows without provider IDs cannot be reliably deduplicated against one another.
- Deleted or unlinked records remain visible to administrators when retained; staff reports require current authorized conversation links. Normalized-number first-contact history can predate a currently visible conversation alias without exposing the old message contents.
- Pre-migration delivery history is only the retained message snapshot. Later callbacks supply chronological metadata. There is no complete historical status series.
- Source/country gaps are not inferred from unrelated fields. Pipeline values, historical pipeline transitions and decorative sparklines are omitted where reliable data is absent.

## Performance and freshness

Aggregation, deduplication and first-contact MIN use SQL CTEs, window functions and grouped source proofs; no unbounded message/customer ORM loads or per-row queries. A metrics request performs one aggregate; drill-down performs one count and one bounded read. A regression checks the three-query combined budget independently of event count. Staff details use one bounded account lookup rather than N+1 queries. The first-contact and attribution proofs intentionally inspect retained history, so latency still grows with database size; this is not an O(1) summary store.

The client refreshes every 30 seconds while visible and no detail dialog is open. Requests are canceled when superseded, no full page reload occurs, filters/pagination/focus persist and transient errors retain the last successful display. There is no shared cross-user cache. Before rollout, measure the scoped API against a staging copy representative of production size and inspect PostgreSQL EXPLAIN (ANALYZE, BUFFERS), particularly first-contact and grouped proof CTEs. No production-sized latency claim is made from isolated tests. If staging fails latency targets, add an incremental summary with the same authorization contract instead of weakening scope or showing approximate counts.

## Deployment and rollback

1. Review and merge the PR only through the normal approval process; this task does not deploy.
2. Back up the database. Validate migration history has head `20261106_25`; apply `alembic upgrade head` in staging first.
3. With application writers paused for the additive migration/backfill, upgrade to `20261107_26`, then start the new application version. Avoid mixed-version writers during the identity backfill/cutover.
4. Smoke-test `/crm` with admin and restricted staff, custom dates, scoped drill-downs, empty ranges and simulated refresh errors. Check counts against retained source IDs. Use mocked sends; do not message customers.
5. Keep existing Global AI OFF and existing production encoding/configuration unchanged. No environment-variable changes are required by this feature.
6. To roll back application behavior, restore the prior application version. The migration downgrade deliberately retains all added columns, tables, indices and reporting history; it does not drop data. Re-upgrade is idempotent. Do not delete additive tracking to roll back UI behavior.

SQLite and PostgreSQL tests exercise pre-26 schema, upgrade twice, retention-safe downgrade/re-upgrade, preserved consent/AI, future stable actor snapshots and all main activity/staff drill-down queries. Browser tests use isolated fixtures and intercepted provider traffic, desktop and 320/360/390/430px widths, keyboard focus, URL filters, modal detail pages and last-good-data retention. See the PR for the final complete regression run results. No production migration, environment change, WhatsApp send, deploy or merge is part of this implementation.

## Verification results

- New dashboard suite: 24 passed, including SQLite and actual PostgreSQL migrations/queries and Chromium responsive checks. Final route/ordering rerun: 22 analytics/browser checks passed.
- Existing CRM/Inbox/Quick Messages/permissions/assignment/collaboration/reply queues, consent, Saved Voices, media/collections, AI controls and migration/diagnostic suites: 441 passed, 11 PostgreSQL-only cases skipped in the SQLite run.
- Those 11 PostgreSQL cases were run separately in isolated local schemas (Saved Voices, Global AI concurrency, consent, collections, learning/voice migrations and outbound media). The learning migration's two SQLite variants were repeated in that selection.
- JavaScript syntax, Python compilation and `git diff --check` passed. Existing UTC/dependency deprecation warnings remain.
- No production data or services were changed. Fixture screenshots contain invented test names and mocked activity, not customer data.
