# Loyalty Phase 2A — historical customer recovery

Baseline main: `46032a1` (merged PR #207). Feature branch: `feature/loyalty-phase2a-historical-reconciliation`. Additive migration: `20261124_56`, parent `20261124_55`.

## Findings and release decision

**Production reconciliation remains NO-GO until the owner reviews a fresh read-only report and specific recovery manifests.** This implementation does not access production, deploy, activate loyalty or execute recovery. The global programme remains OFF; the new recovery/retrieval endpoints reject an enabled programme. No Railway variables or Shopify permissions are changed.

The old preview's `missing_customer_details` label actually means **missing authoritative customer attribution**, not necessarily absent names/phones. `SalesOrder.customer_id` is optional; the previous order-entry path commonly stored `customer_name` and `whatsapp_phone` without an internal Customer selection. `loyalty_service.preview_migration` deliberately rejected unlinked orders rather than equating a phone match with a customer. It generated review suggestions but no automatic association or customer creation.

The reported 16 orders / zero customers is reproduced by tests with 16 paid/completed SalesOrders containing valid names and normalized phones but null Customer IDs. All 16 can be proposed for reviewed association; their commercial records remain unlinked/unchanged, and membership migration is still a separate approval. **The actual production presence and validity of names, phones, payment and completion evidence have not been inspected here.** Null IDs are the supported explanation, not a claim that all production identities or payments have been verified.

Relevant existing data:

- `models.Customer`: unique name, optional mobile; no automatic phone uniqueness or Shopify customer ID.
- `models.SalesOrder`: optional Customer ID, name/mobile, conversation and Shopify references, payment and completion snapshots.
- `models.WhatsAppConversation`: name/mobile and inbox state; it has no authoritative Customer ID. Message bodies are never searched for recovery.
- `POSCheckout`: original customer fields in its frozen snapshot, exact SalesOrder reference, status and completion evidence. No POS or financial state is changed.
- Existing `LoyaltyOrderIdentity`: immutable, loyalty-only reviewed attribution; does not rewrite `SalesOrder.customer_id`.
- Existing Shopify order lifecycle processing (`shopify_order_lifecycle_service.process_orders_create`) only matches known Ledger order IDs/references; unmatched orders are IGNORED. It is **not a complete historical Shopify importer**. Product catalogue import does not establish order-history coverage.

## Immediate read-only Railway diagnostic (works before this PR)

Open the authorized CRM service's Railway browser Console. Copy the **entire command** from:

[`scripts/loyalty-phase2a-readonly-console.txt`](../scripts/loyalty-phase2a-readonly-console.txt)

It starts with `cd /app` and uses a Python heredoc. It imports only the existing database engine, never application startup. PostgreSQL `SET TRANSACTION READ ONLY` is mandatory and verified, and the transaction is rolled back. The output includes migration, programme boolean, total customer count, per-channel counts and at most 200 sanitized order evidence rows. It prints neither names nor phone values, credentials, notes or messages. Presence of a phone string is not a claim of valid normalization. Payment/completion fields are source evidence, not a fabricated eligibility verdict. Aggregate counts are not limited to the first 200 rows.

Expected status: `READ_ONLY_LOCAL_DIAGNOSTIC_COMPLETE`, `transaction_read_only=true`, programme `enabled=false`. If unavailable, stop and report only the sanitized status; do not paste credentials or raw database errors.

After separately approved deployment of this PR, the richer canonical diagnostic is:

```sh
cd /app
python -m scripts.validate_loyalty_recovery_readonly
```

To explicitly examine accessible Shopify history as well (read-only, no local evidence persistence):

```sh
cd /app
python -m scripts.validate_loyalty_recovery_readonly --shopify --max-pages 1000
```

The default page budget is 10. The maximum is 1,000 pages of 20 orders. A budget limit or query/cursor failure means incomplete coverage. The script reports exact normalized identity presence, conflict/candidate counts, payment/completion evidence and order references without printing names/phones. It compares accessible Shopify GIDs against local GIDs, checks actual scopes/shop identity and probes optional protected customer fields separately. No app workers start, no recovery runs and no Shopify mutation is sent. A successful local diagnostic may still contain a failed/incomplete Shopify subsection: inspect both statuses.

## Reviewed recovery workflow

Open Loyalty → Historical Customer Migration. KPI cards show orders by channel, authoritative/missing links, recoverable identity information, review cases, currently eligible linked customers, missing payment/completion evidence, duplicate candidates and coverage.

Access requirements reuse existing grants:

- `loyalty.migration.preview`: aggregate diagnostics and membership manifests.
- Additionally `loyalty.members.manage`: individual identity evidence, proposals, plan history and plan details.
- Additionally `loyalty.migration.execute`: recovery approval/execution and explicitly requested Shopify evidence retrieval.
- Existing CSRF/session checks remain mandatory. Administrators use existing administrator authority; no new weaker role or credential path is added.

Order lists mask mobiles; raw names/mobiles are available only in the management-authorized individual evidence/plan view where needed for review. Audit/report diagnostics are sanitized. No notification is sent.

1. **Review orders.** Choose LOCAL or retrieved SHOPIFY evidence. Inspect the individual source, conflicts and suggested Customer ID. Conversation/POS snapshots are supporting evidence, not silent replacements for an authoritative Customer association.
2. **Associate an existing customer.** Choose the explicit internal Customer ID. Exact normalized phone plus normalized name, a single candidate and consistent sources permit **bulk preview**, up to 100 orders. Shared/missing/conflicting identities cannot enter bulk recovery. Individual uncertain associations require explicit management review and reason; they are never committed automatically.
3. **Create a reviewed customer**, only if a legitimate source has a usable name/mobile, no conflicting evidence, no existing normalized phone or name collision and is not designated test data. Creation is individual, never bulk. Sri Lankan local forms normalize to `+94`; supported international forms retain their country. Missing mobile or a generic walk-in name is not invented. Duplicate names/numbers require reviewing existing Customers, not adding a suffix or merging them.
4. **Preview** persists only a recovery plan/audit with shop, exact source/target/candidate-set fingerprints and digest. It creates no customer, mapping or membership.
5. **Approve** the displayed digest with a meaningful management evidence/reason. The browser fills approval fields only; execution fields remain empty until an explicit approval succeeds. Approval expires after 15 minutes before execution.
6. **Commit** the approved recovery separately. Sources/targets are rechecked and row-locked; a changed shared-phone candidate set invalidates approval. The shop must still match, programme must still be OFF and no existing authoritative association may be reassigned. Changed evidence/identity/collisions fail closed; generate a new preview. The config/plan locks and idempotent COMPLETE state prevent duplicate execution and concurrent double creation. Database uniqueness conflicts produce a sanitized review response.
7. Recovery writes only approved Customers when necessary, loyalty-only mappings, resolved identity reviews, plan outcome and append-only audit. It does **not** create a SalesOrder, payment, journal, inventory movement, POS checkout, membership or Shopify record. Customer-creation outbox hooks are suppressed for this reviewed transaction so creation cannot implicitly activate membership.
8. Reload and select an existing recovery plan to inspect/resume it. Never edit PostgreSQL rows manually to change a plan's status or bypass its digest.
9. Generate a **new membership migration preview** after recovery. Review net paid/completion evidence and separately approve/execute membership batches. Original commercial Customer IDs stay unchanged; loyalty uses its immutable approved mapping. Unpaid, cancelled, test, uncompleted or fully refunded purchases remain ineligible.

Legacy explicit identity resolution no longer creates membership as a side effect. It retains existing audited attribution and permission semantics. New recovery uses the stronger preview/approve/commit workflow; do not use identity verification to invent financial evidence.

## Shopify historical evidence, limits and freshness

`loyalty_shopify_history.py` reuses `ShopifyCatalogClient` authentication and configured API version. No credentials, scopes, webhooks or Shopify data are changed.

The explicit Start retrieval action verifies configured shop identity and `read_orders`. The probe also records `read_all_orders`, oldest accessible order date and whether the normal scope window may restrict older history. Protected `order.customer { id displayName phone }` is a separate optional query. A denial/null result never becomes an invented customer and never invalidates the independent financial read. No shipping address or customer-message content is requested. An optional query succeeding does not guarantee every order has identity data or prove future protected-data approval.

Each explicitly requested page reads 20 orders through the existing GraphQL client. It validates page bounds, `hasNextPage`, end cursor, cursor progress, duplicate GIDs, required financial fields and the scan's creation-time upper bound. It uses `status:any`, so closed/cancelled/test orders can be inspected and excluded honestly. At most 1,000 pages are allowed. Query, timeout, scope, identity, malformed-page or budget failures leave NEEDS_REVIEW; incomplete evidence never qualifies. There is no automatic background scan or recovery.

Rows are staged in **separate loyalty evidence tables**, never imported as commercial SalesOrders. Cursor/progress persists per page. Load scan progress after a lost response; the expected page number prevents replaying a committed page. Existing client throttling/API errors fail closed. Repeated Start actions produce separate audited scans; they do not create commercial duplicates.

PAGINATION_EXHAUSTED means all pages of the accessible query were exhausted, **not complete Shopify historical coverage**. `read_all_orders` does not prove local import completeness, protected access, absence of excluded records or a consistent snapshot. Concurrent Shopify edits remain possible and scans are not atomic. Failed scans must be reviewed/restarted. Large stores beyond 20,000 accessible orders require an explicitly reviewed extension; the implementation does not silently truncate them.

A staged order can contribute to eligibility only if:

- its configured shop matches and its scan exhausted pagination;
- management approved its current identity/financial fingerprint;
- evidence is less than 24 hours old;
- it is non-test, non-cancelled, fully FULFILLED, has positive LKR paid revenue, zero outstanding and a supported paid/refund status;
- verified cumulative refunds leave positive qualifying net revenue;
- no commercial SalesOrder already references its Shopify GID.

FULFILLED is explicitly Shopify fulfillment-completion evidence, not proof of physical delivery. `closedAt` is used only when present; no delivery date is invented. Remote evidence has no local accounting/stock effect. If the same GID exists locally, **the existing CRM/POS record owns eligibility**, even if pending/ineligible; remote history can never bypass a REVIEW POS operation or compensate for missing local financial verification.

A rescan changing financial/customer data invalidates the prior fingerprint approval. Missing optional protected data likewise cannot reuse old identity as current verified evidence. Historical evidence older than 24 hours is held from eligibility until revalidated. During this recovery phase all retrieval/reconciliation requires programme OFF; before a future live programme rollout, plan an OFF maintenance/rescan window or separately review an ongoing read-only refresh design. Do not activate a programme relying on stale remote-only evidence.

Membership previews include reviewed remote sources, account for duplicate/missing sources and recheck fresh history before approved execution. Historic activation may have no local first-order FK when the first source is Shopify-only; the actual source reference remains in history. No synthetic local order is manufactured for that FK.

## Migration usability

- KPI summaries replace the old extremely wide one-row JSON table.
- Separate `/crm/loyalty/migration/runs/{id}` page shows readable outcomes and paginated results, 100 rows/page.
- IDs and digests use nonwrapping, scrollable/copyable controls. Copy fallback selects the full value if browser clipboard permission is unavailable.
- Selecting a preview fills approval fields but does not approve it. Explicit approval fills execution fields; a page refresh reloads durable status.
- The original digest check, 15-minute approval-start window and 100-customer execution batches remain intact.
- Sri Lankan date pickers convert start-day 00:00 and inclusive end-day's following 00:00 at `+05:30` to UTC. Server storage stays UTC; older UTC API callers remain supported.
- Desktop and Android-width tests check viewport overflow and complete review/approval flow. Physical Android acceptance remains separate.

Screenshots use synthetic data only:

- [Desktop recovery](images/loyalty2a/recovery-desktop.png)
- [Mobile recovery](images/loyalty2a/recovery-mobile.png)
- [Mobile detailed results](images/loyalty2a/results-mobile.png)

## Migration, deployment and rollback

Revision `20261124_56` creates only `loyalty_history_scans`, `loyalty_historical_orders` and `loyalty_recovery_plans`, with scoped GID/request-key uniqueness and foreign keys. Existing commercial tables, Customers, payments, audit history, SKUs/barcodes and stock are not rewritten. Approved Customer creation happens only through separately authorized recovery execution, never in a schema migration.

No new dependency or environment variable is required. After review/merge/release approval:

1. Back up and confirm revision `20261124_55`, programme OFF and correct existing shop configuration.
2. Run the immediate read-only diagnostic and review actual missing links, identities, payment/completion exclusions and source coverage.
3. Apply `alembic upgrade 20261124_56` through the established deployment method **before matching application code**. Do not deploy new code against revision 55.
4. Smoke-test authorized/restricted management views and OFF-state guards. No recovery or membership execution is a smoke-test requirement.
5. Obtain separate approval for any local evidence retrieval, customer creation/association and membership migration manifests. Do not infer that code release approves production data changes.
6. Keep loyalty OFF. Do not modify POS REVIEW operations, Shopify orders, financial transactions or production variables to make historical results eligible.

Preferred rollback: keep programme OFF, retain all staged/audited evidence and stop further recovery. Deploy old compatible code if required while retaining additive tables. Already approved Customer creation and immutable local mappings are **not automatically undone**; preserve them and their audit history. Never delete financial rows or reset the database.

Only after explicit export/destructive-schema approval and compatible old code: `alembic downgrade 20261124_55` drops the three new tables. It does not delete Customers, undo mappings in revision 55, reverse orders/payments or restore financial state. Retain exported recovery evidence/audits; downgrading is not a safe way to reverse a mistaken association. A conflicting immutable attribution needs a separately reviewed corrective design, not manual overwrites.

## Acceptance and remaining blockers

1. Run read-only production report; confirm actual names/mobile availability and financial/completion evidence for the 16 orders. Review malformed/missing/shared/international numbers individually.
2. Demonstrate preview-only changes no customer/member/finance counts. Review one existing-Customer association and one legitimate creation on an isolated staging database first.
3. Approve exact digest, execute once, refresh/retry and verify one mapping/customer, original order unchanged, zero membership/financial mutations. Simulate source/target/shop changes and expiry.
4. Review missing `read_all_orders`/protected data; never request new scopes automatically. Exhaust accessible pages or record incomplete coverage explicitly. Verify duplicates with existing CRM/POS references are excluded.
5. Review new membership manifest and financial source evidence separately. Refunds/test/cancellation/incompletion remain excluded. Keep programme OFF.
6. Desktop and real Android: IDs copy in full, dates represent Sri Lankan days, approval/execution remain separate, back/reload opens durable results and staff permissions are enforced.
7. Confirm WhatsApp messaging/read/unread, POS, receipt printing, barcode, inventory, supplier and accounting paths remain unchanged.

Production runtime/API scopes, actual protected-data availability, the actual 16 identities/payment evidence and complete Shopify historical coverage were **not accessible here**. No live customer reconciliation, membership migration, Shopify request, financial mutation, deployment or programme activation was performed during implementation. Test counts and remaining skips are recorded separately in the PR/results manifest.

## Retrieved automated results

**479 distinct tests passed, including 45 new Phase 2A cases.** No unresolved failures or skips. The commerce SQLite run skipped one PostgreSQL-only draft retry case; it subsequently passed in the 53-case PostgreSQL draft suite. Runs overlap; [per-run counts](loyalty-phase2a-test-results.json) distinguish them.

- Existing plus Phase 2A loyalty/real browser/PostgreSQL combined run: 106 passed. Subsequent final recovery/approval and PostgreSQL run: 43 passed, including one additional shared-phone-candidate change case.
- CRM/staff/payment/Shopify draft/POS daily/pilot/synthetic/purchasing/inventory: 265 passed, 1 SQLite-specific skip resolved separately.
- WhatsApp Inbox/manual unread/mobile/finance: 82 passed.
- Dedicated global PostgreSQL: 53 Shopify draft cases and 18 fulfillment cases passed.
- Final desktop/mobile layout: 5 passed. Pre-deployment Console heredoc also ran read-only against revision 55 in disposable PostgreSQL.
- Python compilation, JavaScript syntax checking, migration-head inspection (`20261124_56`) and `git diff --check` passed. Existing framework/deprecation warnings remain.

Reproduce with disposable local databases only:

```sh
python -m pytest -q tests/test_loyalty.py tests/test_loyalty_recovery.py tests/test_loyalty_postgres.py tests/test_loyalty_recovery_postgres.py tests/test_loyalty_browser.py tests/test_loyalty_recovery_browser.py
```

Set `TEST_POSTGRESQL_URL` to a localhost disposable database named `pixiepinks_test`; browser tests require existing Playwright and `/usr/bin/chromium`. For the legacy global-dialect concurrency cases, set `DATABASE_URL` to a separate disposable PostgreSQL database, use `PGCLIENTENCODING=UTF8` where necessary and run the Shopify draft/fulfillment suites. Never point these tests at production.
