# WhatsApp Sales Pipeline

This is an additive module in the existing CRM, directly below **Overview**.
It does not send customer WhatsApp messages, create customers, change prices,
post payments, reserve inventory or call Shopify. The existing authenticated
Create Order flow and reconciliation remain the only order path.

## Architecture and actual status mappings

`Lead.status` remains the canonical Inbox sales status. Reply workload/read/unread
state is separate and unchanged. Central mappings in `sales_pipeline.py` are:

| Existing status | Pipeline stage |
|---|---|
| NEW | New WhatsApp Inquiries |
| REPLIED, AWAITING_CUSTOMER_REPLY, NOT_RESPONDING, INTERESTED, FOLLOW_UP, NEGOTIATING, OUT_OF_STOCK | Interested / Follow-up |
| PAYMENT_PENDING, PAID | Payment Pending (PAID is a lead label, not payment evidence) |
| FUTURE_BUYER | Future Buyers |
| ORDER_CONFIRMED, DISPATCHED, DELIVERED | Confirmed only with an authoritative linked CRM order |
| LOST, NOT_INTERESTED, INVALID_SPAM | Closed; new spam opportunities are excluded |

Existing confirmed labels without an authoritative association require review;
show them in Interested rather than assert a successful conversion. Never recreate
an order to fix a missing link. Historical support/unclassified chats are not
silently declared sales: explicitly classify/link their lead in the existing Inbox.
No opportunity is created per inbound message.

One active opportunity per conversation is enforced by a unique nullable active
conversation key. Explicit new purchase intent archives the previous opportunity,
retaining its snapshot, order and history. Historical opportunities cannot change
the current Inbox status. Explicit flushes are tracked until commit so source updates cannot bypass synchronization. Current status edits synchronize in the same database
transaction using a gated Session commit hook; no synchronization worker loop.
Existing assignments use `conversation_access.assign`, its ownership checks,
assignment version, audits and notifications. Pipeline edits require the current
conversation owner or administrator; shares retain read-only pipeline access.
Managers see only their existing authorized conversation scope.

`SalesOpportunity` stores a bounded JSON commitment snapshot plus typed indexed
Date columns and Numeric(14,2) amounts. Monetary JSON values are decimal strings;
no floating-point money is used for backend calculations. Linked order identity is
unique across opportunities. Verified `SalesPayment.amount` is order-applied money,
so retained excess from PR #214 cannot inflate a pending balance or forecast.
Unknown amounts remain unknown and are counted separately. Confirmed order value,
expected payments, potential purchases and verified payments are distinct figures,
not realized-revenue reporting. Conversion cohort is the selected opportunity
creation-date window (including closed/lost unless a stage is selected), not an inferred customer acquisition metric. Staff attribution is the current conversation owner, not a historical commission report.

`SalesPipelineEvent` retains previous/current values, actor ID, timestamp and
idempotency key. Migration installs database UPDATE/DELETE rejection triggers for
the audit. Compare-and-swap opportunity versions reject stale edits. Conversation
locks serialize creation and edits; request keys prevent replayed effects.

## Migration and deployment — administrator approval required

Migration **20261126_61**, parent **20261126_60**, creates:

- `sales_opportunities`: unique current conversation and linked order, conversation,
  lead, inquiry date and commitment/follow-up date indexes.
- `sales_pipeline_events`: immutable history and unique action key.
- `sales_pipeline_reminders`: unique opportunity/version/recipient/kind/due time;
  due-state index for durable scheduling.
- `sales_pipeline_control`: administrator-configured reminder timing.

Deploy using the existing backup/migration procedure with both new gates OFF:

```
SALES_PIPELINE_ENABLED=false
SALES_PIPELINE_REMINDERS_ENABLED=false
```

These are the only new environment flags. Do not change them during development.
Preserve existing loyalty ON, AI, Shopify, POS, hardware and notification settings.
Do not add Shopify permissions or change webhook configuration.

After approved deployment, in the Railway browser Console:

```bash
cd /app
python -m scripts.validate_sales_pipeline_readonly
```

This imports no application startup, starts a PostgreSQL READ ONLY transaction,
uses a statement timeout, prints sanitized JSON and rolls back. It reports the
migration, tables/indexes, flags, mappings, classified lead counts, unmapped
statuses, eligible staff, notification readiness and blockers. It prints neither
credentials nor customer names, phones or messages. It is not a live Meta/push test.

After reviewing this output, the administrator can separately approve enabling
`SALES_PIPELINE_ENABLED=true`. No historical backfill runs automatically. New
classified leads/status edits may create their current opportunity once enabled.
Review a synthetic/staging rollout before production use.

## Controlled historical backfill

Sign in as an administrator. Use the same-origin browser Console on the CRM;
these commands require a deployed, approved enabled pipeline. GET is read-only:

```javascript
const preview = await fetch('/crm/sales-pipeline/backfill', {cache:'no-store'}).then(r=>r.json());
console.log(preview);
```

Review classified eligible conversations, status distribution, actual follow-up
fields, absent commitment dates, exact-conversation order/customer candidates,
unlinked legacy leads, unmapped statuses and excluded spam. Candidate orders are
not automatically linked. No registered customer is inferred by phone/name. Notes
are not parsed into invented dates/amounts. Review the digest and obtain explicit
owner approval before running the following write action:

```javascript
const csrf = await fetch('/crm/whatsapp/api/work-csrf', {cache:'no-store'}).then(r=>r.json());
const applied = await fetch('/crm/sales-pipeline/backfill', {
  method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf.csrf_token},
  body:JSON.stringify({digest:preview.digest, confirmation:'APPLY PIPELINE BACKFILL'})
}).then(async r=>({status:r.status, body:await r.json()}));
console.log(applied);
```

Apply compares a fresh manifest and locked per-lead fingerprint. Repeating a fresh
preview proposes no duplicate current opportunities. A stale digest is rejected.
Keep the reviewed digest for rollback; all inserted records carry its provenance.

Approved rollback first previews unchanged records:

```javascript
const rollback = await fetch('/crm/sales-pipeline/backfill/rollback?digest='+encodeURIComponent(preview.digest), {cache:'no-store'}).then(r=>r.json());
console.log(rollback);
```

Only after reviewing and authorizing it:

```javascript
await fetch('/crm/sales-pipeline/backfill/rollback', {
 method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf.csrf_token},
 body:JSON.stringify({digest:preview.digest,confirmation:'ARCHIVE UNCHANGED BACKFILL'})
});
```

This archives unchanged unlinked entries, cancels their pending schedules and
preserves audit/history and original Inbox statuses. Edited/linked records stop
rollback for manual review. It does not delete financial/customer/Shopify records.

## Sales staff guide

1. Use the new Sales Pipeline navigation entry. Default dates show **all active
   opportunities**, including later months. Search/filter only your authorized data.
2. Select a card to inspect the full note, dates, balances, order status and history.
3. Update Status, Set Payment Date or Set Future Date opens the commitment form.
   Choose **Date Not Confirmed**, an **Exact** customer promise or an approximate
   **Range**. Internal follow-up is a separate date; amounts are LKR expectations.
4. Save. A stale-version conflict means reload/review; do not overwrite another
   staff member. Cancelling a drag/form makes no server change.
5. In the Inbox, selecting an eligible status opens the compact commitment editor
   for existing opportunities. Newly classified inquiries get it after status save.
6. Open WhatsApp Chat returns to the exact existing conversation. Create Order
   opens its existing draft editor, with purchaser contact handled by that editor;
   product interest/notes are context, not permission to override catalog pricing.
   International purchaser and local recipient numbers remain separate.
7. Saving from a pipeline context atomically associates the existing validated draft and reserves one durable `pipeline-order:<opportunity>` identity, even when the browser retries with another random key. This does **not** mean conversion. A confirmed
   authorized CRM transition later converts only that explicit linked opportunity.
   An uncertain Shopify state remains reviewable and cannot be forced converted.
8. A lost response is recovered using that same intent identity and returns the existing draft. Validation failure rolls back both draft and link. Existing older unlinked records may be associated explicitly after review; never create another order for recovery.
9. For an actual later purchase, choose New purchase intent after closing/converting
   the prior intent. Preserve its order and history.

## Reminders and Android

The existing staff notification worker scans the durable queue. New pipeline
reminders use its existing REMINDER preference/type, with a pipeline event key and
opportunity deep link; no replacement notification architecture or customer send.

Default time is 09:00 Asia/Colombo. Payment offsets are -1/0/+1 days; future purchase
-3/-1/0; internal follow-up on its date. Old historical reminder dates are not
replayed. Assignment/date/config changes supersede old versions; closure/conversion
stops reminders. Claims use PostgreSQL SKIP LOCKED and unique notification keys.
Only the assigned eligible user receives reminders. Unassigned/overdue management
visibility is available on the scoped board/report, not unsolicited team alerts.

Administrators use **Management insight → Reminder settings** to set time/offsets.
That does not activate delivery. `SALES_PIPELINE_REMINDERS_ENABLED=true` requires
separate approval. Existing notification preferences, sound opt-in, session/device
checks, HTTPS, browser permission and `STAFF_PUSH_ENABLED`/VAPID configuration still
apply. Do not expose or replace existing keys. No physical Android push or sound
delivery has been verified in this environment.

## Acceptance and visual review

Actual synthetic screenshots are under `docs/images/sales-pipeline/`:
1648×928, 1920×1080, 1366×768, payment/future selected cards and forms, filters,
Android board/details. These are rendered UI screenshots, not design mockups.
The attached reference guided the four pastels, five columns, compact cards,
right detail panel, green actions, priorities and date highlights. Existing
PixiePinks navigation/header are preserved. Differences: existing CRM sidebar is
wider, fonts/icons follow existing system styling, text varies with real data,
1366px uses contained horizontal column scrolling, and integration state is honest
configuration evidence rather than an unverified green Connected claim. No 100%
pixel-match or actual Android push/physical-device claim is made.

Owner acceptance checklist:

- [ ] Read-only readiness output reviewed; correct migration and flags.
- [ ] All five stages and shared/owner/admin scope verified on desktop and Android.
- [ ] Inbox/pipeline stage/date edits synchronize; refresh preserves context.
- [ ] Genuine unread counts, manual colored markers and read receipts unchanged.
- [ ] Exact/range/unknown dates and Colombo midnight/week boundaries checked.
- [ ] Partial/unknown balances, unpaid/COD confirmed orders remain accurate.
- [ ] Cancelled drag, stale edit and failed request leave original state intact.
- [ ] Existing order linking and review locks work; no duplicate creation.
- [ ] Approved backfill preview/digest/apply and guarded archive tested in staging.
- [ ] Assigned-only bell alerts deduplicate and supersede after edits/reassignment.
- [ ] Android push/sound separately tested on an authorized real device.

## Rollback, limits and remaining operator checks

Disable reminders first, then pipeline, only with release authorization. Existing
Inbox/order/POS paths remain usable and history is preserved. Use a compatible
code rollback retaining revision 61; older builds that cannot resolve its Alembic
revision cannot safely run normal startup. Schema downgrade refuses when any
opportunity exists and never erases history to make rollback succeed.

The projection uses batched queries and one latest inbound per conversation, not
full chat downloads. Each column is paged by 50; KPIs aggregate the same filtered
scope. A deliberate 10,000-opportunity projection budget fails clearly, rather
than silently truncating totals. Staff/date/priority filters narrow SQL first;
very large installations need a subsequent database aggregation/export worker.
Mappings are reviewed source-level mappings; arbitrary runtime remapping is not
exposed because it could misrepresent order conversion. Live Meta health, actual
Railway deployment/backfill and real Android push remain operator acceptance.

### Reproduce the synthetic screenshots locally

Use a new disposable filename (the fixture refuses non-/tmp and non-SQLite URLs).
It starts only localhost and explicitly suppresses application startup workers:

```bash
DATABASE_URL=sqlite:////tmp/pipeline-visual-review.db python scripts/sales_pipeline_visual_fixture.py
# In another terminal, from the repository root:
python scripts/capture_sales_pipeline.py
```

The fixture's fixed login is synthetic-only and is never installed in production.
Stop the localhost server afterwards. Chromium and Playwright are development/test
requirements only; no new runtime frontend dependency is introduced.

## Retrieved verification results

See `sales-pipeline-test-results.json` for distinct final test outcomes and exact
screenshot dimensions. Overlapping reruns are counted once. Earlier integration
failures and the SQLite-skipped PostgreSQL case were corrected/rerun successfully.
No tests were run against production. Representative invocation:

```bash
DATABASE_URL=sqlite:////tmp/pipeline-tests.db \
TEST_POSTGRESQL_URL=postgresql://agent@127.0.0.1:55429/pixiepinks_test \
python -m pytest -q tests/test_sales_pipeline.py tests/test_sales_pipeline_browser.py
```

The PostgreSQL fixture refuses a non-local or non-test database. Existing Inbox,
lead status, work queue, manual unread, customer registration/recipient, loyalty,
Sales Orders, payment, Shopify-draft and staff-notification regressions were run
separately on disposable databases. No financial or hardware test targeted live
services. No genuine transaction, Shopify inventory or old REVIEW queue was changed.
