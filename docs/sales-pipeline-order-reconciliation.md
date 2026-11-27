# Sales Pipeline — administrator order reconciliation

## Recovery and scope

The original worktree and feature branch survived the runtime interruption. Read-only
Git inspection found no modified, staged or untracked implementation files: the
attempted service write had not persisted. Missing implementation was reconstructed
on **feature/sales-pipeline-order-reconciliation**, from main **796c107**, the merged
PR #216. No unrelated worktree was reset or modified. Coherent checkpoints were
committed and pushed before final verification.

This is local order association, **not financial reconciliation**. It makes no
Shopify request and creates no CRM/Shopify order, invoice, payment, loyalty action,
notification or inventory movement. Production candidates from the supplied diagnostic
remain future review targets; production reconciliation was not executed here.

## Architecture and compatibility

- `sales_pipeline_reconciliation.py` batches authorized opportunity, conversation,
  lead, existing order, registered customer, verified payment, unresolved Shopify
  operation and association metadata. It never fetches historical WhatsApp messages.
- The existing read-only diagnostic remains the association-diagnosis source.
  The UI uses stronger eligibility checks for committing an association, rather than
  treating a preliminary diagnostic candidate as approval. No fuzzy name, amount,
  date or phone matching creates a candidate: **exact conversation ID is required**.
  Phone normalization checks existing purchaser consistency; it does not establish
  a customer association. Existing customer IDs and any conflicting customer data
  remain unchanged.
- Orders in this CRM schema have no separate shop-ID column. Business scope is the
  existing authorized conversation and its recorded local order, with POS orders
  excluded by existing `sales_pipeline.convertible()`. There is no cross-shop name
  search or external identity inference. Live Shopify identity is not newly verified.
- A unique existing order must meet `convertible()`. Missing order, contradictory
  order-ID fields, wrong lead/conversation/purchaser/customer, a different purchase
  intent already owning the order, uncertain synchronization, active operations or
  unresolved reconciliation locks fail closed. Unsupported operation states require
  investigation, even when a human considers a prior failure safe.
- JSON opportunity metadata holds investigation state/note. Existing unique nullable
  `sales_opportunities.order_id` and append-only `sales_pipeline_events` are reused.
  **No migration required; retain revision `20261126_61`. No new flag.** Existing
  PostgreSQL audit trigger and order uniqueness are checked by the read-only diagnostic.
- Every reconciliation page/API, preview, write, investigation and history requires
  an active ADMIN with existing WhatsApp permission and `SALES_PIPELINE_ENABLED`.
  Existing CSRF validation applies to POSTs. Managers/cashiers cannot use these APIs.
  Administrative investigation data is excluded from normal staff pipeline projections
  and history; no customer or order access is widened.

## Transactions and evidence

Bulk requests contain 1–25 distinct individually reviewed opportunity/order pairs.
A final preview compares the selected IDs, opportunity version and SHA-256 evidence
digest. The digest covers complete local opportunity, conversation, lead, candidate
orders, customer, payment, operation and line snapshots. A browser preview is not
permanent eligibility evidence.

Commit locks conversations, opportunities, orders, leads and existing evidence rows,
refreshes the ORM identity map, and rechecks every pair **before changing any pair**.
Order/conversation foreign-key locks also serialize related insertions. Existing order
uniqueness remains the final duplicate guard. The entire selection commits atomically;
one invalid/stale item causes rollback, with no silently skipped records. Rejections
include per-record Already Associated, Conflict Detected or Requires Investigation
results; eligible members of a rejected batch are explicitly not committed. Normal
pipeline edits cannot remove or replace an existing association. Historical linking
must use the reviewed administrator path rather than the legacy convenience endpoint.

Request idempotency is bound to administrator ID and full request payload. Repeating
the same request/key returns the saved batch result; different evidence with that key
is rejected. A new key against an already associated candidate is rejected for fresh
review, not treated as another successful association. PostgreSQL concurrent requests
with the same key produce one write and one saved response; different keys produce
one success and one conflict. Deadlocks/transaction conflicts return a sanitized
conflict requiring fresh review; no automatic association retry is added.

Events record unique ID, actor, old/new order and stage/status, conversation/lead IDs,
note, timestamp, batch ID, source, idempotency key, evidence digest and sanitized local
order/payment comparison snapshot. They do not retain purchaser names, phones, messages
or credentials. History remains available after the opportunity becomes an archived
purchase intent. Existing database triggers prevent audit update/deletion.

## Conversion and payment semantics

Association uses existing `convertible()` eligibility and canonical stage mapping.
Delivered and Dispatched lead statuses are retained. A Paid lead requires the explicit
confirmation's disclosed transition to Order Confirmed; this changes **only the lead
and opportunity**, never the actual order status, payment/fulfillment/lifecycle,
received amount, Shopify state or ledger. A Processing order stays Processing.

Confirmed Orders does not mean paid. The board identifies verified local payment,
unpaid/partial amounts or **Payment Verification Required**, alongside actual order
lifecycle. Association cannot synthesize a payment receipt. Paid-label warnings remain
when verified same-currency local payment is insufficient; these summary counts overlap
ready/missing/history categories. Existing KPI and unique-order reporting are reused;
converted opportunities leave open-lead/pending forecasts without duplicating an order.

## Administrator guide

1. Open **Sales Pipeline → Management insight → Order Reconciliation**, or the amber
   review-count link near Confirmed Orders. Staff do not see these controls.
2. Review Ready, Missing, Payment Verification, Conflicts, Reconciled History or All.
   Search names, IDs, existing order references, authorized numbers or assigned staff;
   each table page is bounded to 25 by default (maximum 100).
3. Click **Review**. Compare exact conversation, lead, purchaser and registered customer
   identity, candidate uniqueness, order items/value, actual lifecycle, recorded payment,
   verified local amount, unresolved locks and version. Open existing authorized chat
   or order routes if needed. No green live-Shopify verification is implied.
4. For one candidate choose **Confirm Association**. For bulk, explicitly mark each
   eligible candidate reviewed, select it, then **Preview selected associations**.
   Selections are session-memory only; they are not durable administrator approval.
5. Read all proposed mappings and warnings. Supply a reason and tick the exact review
   confirmation. Only then submit. The server revalidates everything in one transaction.
6. Review the success/history and board. Confirm actual order/payment data are unchanged.
   Returning to the board restores the authenticated user's prior selection/filter and
   independent scroll state. This short-lived browser state contains no message bodies
   and is keyed by user ID; it does not restore another staff member's selection.
7. Missing/ambiguous/unsafe records: record an investigation state/note. This does not
   change customer opportunity status, close it, create an order or verify payment.

An already associated record is visible as Already Associated or Reconciled History.
Do not attempt to recover a lost response using a new order. Keep the original request
key/payload if an administrator needs to establish its saved result.

## Safe correction policy

No unrestricted unlink, deletion or reversal endpoint is provided. If identity is later
questioned, inspect the existing order and immutable history, record a reason, review
current versions and downstream opportunity/reminder/reporting dependencies, and request
a separately approved correction workflow. No automatic reversal is proven safe here.
Never undo payments, loyalty, Shopify or stock to fix a pipeline link. Application
rollback retains associations and audit history.

## Read-only Railway verification

After an approved release, run in the authorized CRM Console:

```bash
cd /app
python -m scripts.validate_sales_pipeline_orders_readonly
```

For authorized local IDs/statuses only:

```bash
cd /app
python -m scripts.validate_sales_pipeline_orders_readonly --details
```

The existing command imports no application startup, explicitly sets PostgreSQL
TRANSACTION READ ONLY, uses a 30-second statement timeout and rolls back. It prints
sanitized JSON including migration, preliminary association reasons, eligibility summary,
active authorized administrator availability, order uniqueness, audit trigger readiness
and blockers. No raw exception details, customer names/numbers/messages, keys or tokens
are printed. It does not verify live Shopify, banking or external payment evidence.
The legacy diagnostic details concern current historical statuses; UI history also
includes archived reconciliations. Summary categories overlap.

Development tests are disposable SQLite/PostgreSQL with synthetic data and mocked
external calls. Never execute mutation tests or seed scripts against Railway.

## Deployment and operator acceptance

1. Review draft PR, retrieved test evidence and actual synthetic screenshots. Owner
   approval is required for merge/deployment. No production operation ran in this task.
2. Retain migration **20261126_61** and existing production variables:

   ```ini
   SALES_PIPELINE_ENABLED=true
   SALES_PIPELINE_REMINDERS_ENABLED=false
   ```

   Do not rerun historical backfill or change reminders, printing, Shopify, POS or
   financial write flags. There is no automatic association or reconciliation job.
3. Run the read-only command. Stop if uniqueness/audit/admin readiness is false or
   evidence contradicts the supplied earlier diagnostic. A new eligible candidate
   count does not authorize writes.
4. Verify staff/manager direct API rejection and all three desktop/Android layouts.
   Keep existing board independent scrolling, fixed details actions/KPIs and polling.
5. Separately obtain administrator approval for the current candidate mappings. The
   production mappings supplied by the owner are **review targets only**, not hardcoded
   application/migration/fixture associations. Inspect each fresh evidence panel.
6. Perform one explicitly approved association first, inspect immutable audit and
   unchanged order/payment/Shopify/inventory state, then separately approve remaining
   eligible mappings individually or via one bounded bulk transaction.
7. Leave missing Paid records intact for individual investigation. Do not create an
   order merely to populate the Confirmed column. Run the read-only diagnostic again.

Rollback: redeploy accepted PR #216 code using normal owner-approved release controls.
No database downgrade/backfill/reset is required. Retain all events, links and
investigation metadata; code rollback does not undo an association. Do not clear links
through SQL. If a downstream correction is necessary, stop for separately reviewed
identity and dependency handling.

## Validation and known limits

**443 distinct automated cases passed; no unresolved failures or skips.** The broad
SQLite run had one conditional skip, subsequently passed explicitly on PostgreSQL.
Final runs: 95 backend/pipeline, 14 browser, 321 broader regressions and 13 additional
PostgreSQL cases. Overlapping reruns are counted once. Existing datetime/deprecation
warnings remain; they are not counted as failures.

See `sales-pipeline-order-reconciliation-results.json` for final retrieved test counts,
and `images/sales-pipeline-reconciliation/` for actual Chromium screenshots using
synthetic records at 1920×1080, 1648×928, 1366×768 and 412×915. These are not production
screenshots or physical Android-device verification. Screenshot tests include dashboard,
evidence, confirmation, bulk preview, investigation, payment warnings, ambiguity,
history and the resulting Confirmed Orders board.

Candidate projection batches metadata over the authorized historical set before
pagination; it does not download customer message history. Very large metadata/history
sets may require further SQL-side pagination; no claim of production latency is made.
History panels show the latest 100 existing events; full append-only records are retained.
Bulk is capped at 25 and all-or-nothing. Unsafe/ambiguous cases are deliberately not
resolvable by guessing. No automatic unlink or external payment verification is provided.

Remaining production operator checks: fresh readiness/evidence, independent review of
current mappings/customer identities, physical Android touch acceptance, release approval
and separate association approval. No production candidate has been linked here.
