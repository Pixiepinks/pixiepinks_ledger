# Phase 1B: controlled Shopify barcode synchronization

## Release status and hard blocker

The reviewed batch UI, durable approval/queue/history, locking, reconciliation and tests are implemented. **The production Shopify write adapter is intentionally unsupported and cannot mutate products. This release is not ready for a live synchronization pilot.** No production migration, configuration, scope change or live product write was performed.

The existing configured API default remains `2026-07`; this release does not change the CRM API version. Barcode-only read diagnostics explicitly request `2026-10` and reject a missing/different served-version header. See the [adapter completion/blocker report](retail-barcode-adapter-completion-report.md) for the latest evidence and validation requirements. Shopify's supplied contract identifies multiple barcode support in `2026-10`. A legacy `barcode: null` response cannot establish that secondary barcode slots are empty. The adapter performs only fixed read-only capability queries: installed `write_products` scope, ProductVariant fields, bulk input fields and the actual `X-Shopify-API-Version` header. Missing access, introspection or version evidence fails closed. Seeing a `barcodes` field is **not** enough to establish its full pagination, typed inputs, custom-code semantics, cross-catalog uniqueness or non-destructive update behavior.

Attempts to retrieve the public official references on 2026-11-18 were denied by the environment's network policy (HTTP proxy 403). This environment has no configured production Shopify connection. Neither the production supported version nor the installed app scopes have been verified here. Do not represent mock schema/state fixtures as Shopify schema verification. `BarcodeClient.write_empty` always raises `MULTI_BARCODE_CONTRACT_UNVERIFIED`; there is no speculative network mutation or caller-supplied GraphQL operation. Both flags can be true and the production adapter will still block approval.

Official references for the next adapter review:

- https://shopify.dev/docs/api/admin-graphql/2026-07/mutations/productVariantsBulkUpdate
- https://shopify.dev/changelog/posts/product-variant-barcode-is-being-replaced-by-barcodes
- https://shopify.dev/docs/apps/build/authentication-authorization/manage-access-scopes

Before a live pilot, implement a separately reviewed, versioned adapter using an independently obtained supported schema and confirmed mutation semantics. It must read **every** barcode slot and prove cross-catalog collision checks are complete; reject missing/deleted/inactive or differently owned variants; never treat truncated connections as empty. For the legacy documented bulk mutation, only `productId`, one variant's `id`/`barcode`, and `allowPartialUpdates:false` belong in the mutation. Never send SKU, price, inventory or a replacement multiple-barcode list speculatively. An internal `P2` value is custom/untyped, never UPC, EAN or GS1. Full multi-barcode read and write fixtures must be validated against that exact adopted schema, not invented from field names. The adapter is a narrow interface used by the queue so the approval and recovery module need not be rewritten.

## Installation and defaults

Additive Alembic revision: **`20261118_43`**, parent **`20261117_42`**. It creates:

- `retail_barcode_jobs`: shop/actor-scoped preview, approval token, fingerprint, expiry and job lifecycle.
- `retail_barcode_items`: unique idempotency key, frozen reviewed identity/local assignment/remote-before snapshots, attempts, leases and before/after verification evidence.
- `retail_barcode_reservations`: unique shop/variant and shop/value reservations for overlapping approved batches and unresolved outcomes.
- `retail_barcode_events`: append-only approval/dispatch/recovery events. SQLite and PostgreSQL triggers reject event updates/deletes and reviewed item snapshot changes.

No existing product/table columns are rewritten. Downgrade retains these tables and evidence; do not delete history to roll back.

Deploy web and worker code together only after review. Back up the database and test `python -m alembic upgrade head` on a staging copy, then apply it before starting the new application. Do not start new code against an older schema: Product Master includes read-back verification metadata from these additive tables.

Default flags:

```
SHOPIFY_PRODUCT_WRITE_ENABLED=false
RETAIL_BARCODE_SYNC_ENABLED=false
RETAIL_BARCODE_SYNC_BATCH_LIMIT=5
```

`RETAIL_ENABLED` remains the existing master switch. Batch limit accepts 1–25. Keep the initial live limit at 1–5 after the adapter blocker is resolved. Flags do not schedule work. There is no automatic all-products synchronization, scope request, app reinstall or environment edit. The dedicated `retail.barcodes.shopify_sync` permission is granted implicitly to active administrators only by default; administrators may explicitly grant the action using existing staff access controls. Every API action checks the grant, tenant and CSRF where it changes state. Workers recheck the approving user's active account and current grant before dispatch.

## Reviewed workflow

1. Select specific variants using the existing Product Master filters/checkboxes and save the selection. Shared supplier codes still open the explicit variant picker; no variant is chosen automatically.
2. Open **Shopify Barcode Sync** and preview the selected batch. Selection ownership is preserved. Preview creation is limited to five per actor/shop/minute and the configured batch limit.
3. Review every candidate: exact Shopify variant/product ID, SKU, committed local code, assignment-ledger ID/version, remote state, reason, last check and status. Currently all production candidates requiring the unverified API contract remain blocked.
4. Once a supported adapter exists, only eligible rows can be approved. Type the exact server-generated `WRITE N BARCODES TO EMPTY FIELDS IN SHOP BATCH ID` confirmation. Approval token, immutable fingerprint, actor/shop, current local snapshot, flags, scope and served API version must match. Tokens expire after ten minutes. A repeated approval does not enqueue a second job. At most ten active batches are accepted per shop.
5. The existing retail background worker dispatches one item per cycle. It claims a lease and rechecks the exact assignment, freshness (seven days), active state, ledger, barcode collisions, independent duplicate-SKU issues, permissions, scope, expiry and complete remote emptiness. Documented SKU sharing is distinct from barcode sharing. Nonempty supplier barcodes are never eligible, even if sharing is documented.
6. A durable dispatch-intent marker is committed **before** the remote call. After a second preflight under the shop lock, the narrow adapter is invoked once. A fresh complete read-back, including collision evidence, must confirm exactly the intended custom barcode before VERIFIED is recorded. A response saying success is insufficient.
7. Review history and export the batch CSV. Formula-like cells are escaped. Batch history pages contain 20 bounded batches; event pages contain 50 events. All UI text is inserted as text, not executable markup. Errors and logs retain controlled codes/job IDs, never upstream payloads, credentials or tokens.

`ELIGIBLE`, `BLOCKED`, `QUEUED`, `CHECKING`, `SENT`, `VERIFIED`, `CONFLICT`, `FAILED` and `UNKNOWN` are distinct item states. Job state is PREVIEW, QUEUED, RUNNING, COMPLETE or CANCELED; COMPLETE means processing ended, **not** that every item succeeded. Read item counts/reasons. UI metrics summarize durable historical outcomes, not a claim about the live catalog. Imported/local/read-back-verified states remain separate; a matching read-only import does not create a VERIFIED event. A successful read-back does not modify the local Shopify snapshot, SKU, price or inventory; a later ordinary catalog import observes remote values while retaining the committed local ledger.

## Concurrency, cancellation and recovery

The existing per-shop database lock serializes preview/approval/dispatch with catalog publication and identifier allocations. Unique reservations prevent overlapping batches. A version or assignment change after preview/approval blocks dispatch; imports/allocations that arrive during the final network boundary wait for the transaction lock. All network reads/calls have bounded timeouts. Claims carry a random token and five-minute lease, preventing an older worker from sending after another worker reclaimed it.

Cancel a PREVIEW/QUEUED batch before dispatch begins; it releases unsent reservations. Cancellation is rejected once a claim/dispatch has begun. Disabling either write flag prevents queued new writes but does not prevent explicitly requested read-only reconciliation.

- Read-only preflight errors retry at 30 and 60 seconds, at most three claims. Scope/schema failures are conflicts, not unbounded retries.
- After the durable SENT marker, the worker **never automatically resends**, including a timeout, API error, partial/user error or crash. It reads back. Even a crash before the actual call after recording intent may leave UNKNOWN; this sacrifices availability to prevent duplicate dispatch.
- A stale CHECKING claim with no SENT evidence can be reclaimed after five minutes and fully checked again. A stale SENT claim only reconciles.
- UNKNOWN and post-dispatch CONFLICT reservations remain pinned. Use **Reconcile read-only** to fetch fresh remote evidence. An empty remote state still means UNKNOWN, not permission to retry the write. Never manually clear a reservation and blindly resubmit.
- VERIFIED releases its reservation. A confirmed pre-dispatch conflict releases unsent reservations. A successful import observing an identifier never impersonates synchronization verification.
- No automated removal, overwrite or remote rollback exists. Investigate uncertain outcomes using event IDs and fresh remote records. Retain evidence and resolve with separately reviewed code/operations if an intentional retry is needed.

The Shopify remote preflight/write boundary is **not an atomic compare-and-swap**. A third-party app or staff member can edit Shopify after the read. Application-local locks cannot stop that. Pilot operations require a controlled window with other product editors paused; complete read-back can detect a mismatch but cannot undo a destructive third-party race. No atomicity claim is made.

## Staging, scope review and eventual pilot

1. Review the migration/code and preserve backups, production variables and scopes. Apply staging migration and run tests below. Verify existing Inbox/AI/notifications/imports/label printing and shared barcode picker.
2. Obtain read-only scope/schema evidence with the actual authorized shop installation. The current production adapter remains unsupported regardless of flags. Do not automatically request `write_products` or change the global API version. Have the owner review any required access change separately.
3. Resolve and test the adapter blocker with supported multi-barcode fixtures and safe custom identifier semantics. Review mutation allowlists and read-back. Repeat concurrency, uncertainty, API-rate and regression tests.
4. Only after separate deployment/scope/flag approvals, keep other catalog writers paused, select **one** known active variant with a committed local barcode and genuinely empty complete barcode state. Confirm remote state manually. Approve exactly that batch. Verify remotely and by ordinary import/scan; then, if successful, expand to at most five reviewed variants.
5. Stop on any unsupported, conflict or unknown result. Do not use a production pilot to discover schema behavior.

## Rollback

Disable `RETAIL_BARCODE_SYNC_ENABLED` and `SHOPIFY_PRODUCT_WRITE_ENABLED` through the approved production process; this task does not do so. Allow a bounded in-flight call to finish or become UNKNOWN, retain history and reconcile it by reads. Roll back application code while retaining the additive schema, local assignments, reservations, audits and label PDFs. The Alembic downgrade is intentionally nondestructive. Never delete a Shopify barcode as a rollback action.

## Test commands

Use the existing project virtualenv and a disposable SQLite URL; all integration transports are mocked. PostgreSQL tests require a localhost database named `pixiepinks_test`, use unique schemas and drop only those test-owned schemas.

```
DATABASE_URL=sqlite:////tmp/phase1b.sqlite \
TEST_POSTGRESQL_URL=postgresql+psycopg2://USER@127.0.0.1:PORT/pixiepinks_test \
python -m pytest tests/test_retail_barcode_sync.py tests/test_retail_barcode_sync_postgres.py \
  tests/test_retail_postgres.py tests/test_retail_phase1.py \
  tests/test_retail_product_filters.py tests/test_compact_sku_price_label.py -q
node --check static/retail.js
```

The Phase 1B fixtures exercise an abstract **test-only** complete multi-barcode contract; no test performs a Shopify product write. The real adapter test asserts even a schema with `barcodes` remains unsupported and direct write invocation raises without HTTP. See the implementation report for actual results, regression commands and residual blockers.
