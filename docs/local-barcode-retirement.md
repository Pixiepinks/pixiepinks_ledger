# Retire a local barcode and use the Shopify barcode

Administrator-only, local CRM action. It does not delete the immutable allocation or write anything to Shopify. No schema migration, write flag or production configuration change is needed.

## Operator procedure (after reviewed deployment)

1. Open Product Master or Barcode Management and search the exact SKU (for the reported item, `4001946`). Verify the product/variant and physically scan the supplier label. Check the **complete** Shopify barcode in Details, rather than concatenating visually wrapped table text. The screenshot appears to show `4792210130832`; verify it before acting.
2. Review History and any barcode-sync/label jobs. Cancel only demonstrably undispatched synchronization work through existing controls, or reconcile uncertain outcomes with their existing separately authorized process. Retirement does not cancel, clear, replay or change any jobs. Finish/review outstanding label hardware actions honestly; do not claim physical output merely to bypass a fence.
3. Administrator chooses **Retire local / Use Shopify**, supplies a reason (10–500 characters), types the exact local barcode (for example `P14`) and confirms the supplier barcode and removal/replacement of old labels/saved PDFs.
4. The version and both barcode values must still match. The product must be active, imported recently (not stale/older than seven days), have a valid Shopify barcode and an existing committed local allocation. No fallback is invented. Missing/different shop, changed values, insufficient permissions or missing CSRF fail closed.
5. On success the active local pointer is cleared. Shopify barcode, SKU, inventory, product data and original allocation/counter remain unchanged. The row shows **Retired: P14**; Details and History retain the original code, reason, administrator, timestamp, allocation ID and preferred Shopify value.
6. New scans and labels use the imported Shopify barcode. The retired local code no longer resolves through that local alias (if its literal value is also the Shopify barcode, scanning that Shopify value still works). Existing shared supplier barcodes continue to use the variant picker. Existing print-size/quiet-zone validation remains mandatory; do not shrink long supplier barcodes to bypass scanner-safe geometry.
7. Physically remove/relabel old local labels and discard previously downloaded PDFs. Already downloaded documents cannot be recalled. Existing server PDF download/reprint and hardware admission/permit checks reject a retained barcode snapshot when it no longer matches the product's effective barcode. No old job is automatically replayed.

This task implements the action; it does **not** execute retirement for SKU4001946 or any production item.

## Safety and implementation

* `retail_service.retire_local_barcode` takes the existing shop write lock, requires active ADMIN plus existing product-manage permission, exact version/value confirmation and label acknowledgement. It changes only `local_barcode`, barcode source/reason, version and update timestamp, and appends an immutable `LOCAL_BARCODE_RETIRED` audit record in the same transaction.
* `RetailIdentifier` remains untouched, permanently reserving the value in its original assignment. Existing allocation checks consult that entire ledger, so retirement cannot free the code for reassignment. Catalogue imports do not restore a cleared local pointer.
* Outstanding barcode jobs/reservations fail closed. Verified/canceled/skipped/legacy-retired histories are retained. Uncertain or dispatching work cannot be silently hidden. QUEUED/GENERATING PDFs and linked hardware work other than CONFIRMED/FAILED prevent retirement. No receipt-only, POS, financial or Shopify behavior is changed.
* Existing snapshot/version checks invalidate old approvals/previews. No local code means no eligible committed local barcode for Shopify synchronization. The action never calls Shopify APIs, changes write flags, removes a manufacturer barcode or modifies immutable job histories.
* `retail_routes.py` exposes the CSRF-protected action; `templates/retail.html` and `static/retail.js` show it only to administrators and include typed-value/reason/physical-label confirmations. Retired values are visible in product rows, Details and the existing history view.

## Validation

Disposable SQLite/PostgreSQL and mocked services only:

* Broad barcode allocation/import/synchronization/scanner, PostgreSQL, daily POS and compact label suite: **190 passed**.
* Final retirement suite: **27 passed**, including PostgreSQL concurrent requests, real Chromium administrator confirmation flow, permission/CSRF/shop/version checks, immutable ledger retention, re-import, non-reuse, sync/label/hardware fences, old PDF/reprint rejection and new label/scanner preference. Runs overlap. No unresolved failures/skips.
* JavaScript syntax and `git diff --check` verified.

## Release and rollback

Review draft PR; obtain separate merge/deployment approval. Deploy the reviewed build through the established process, preserving all existing flags and data. Hard-refresh product pages. No migration is required (head remains20261122_53). Separately authorize the administrator's actual per-product retirement after supplier-label verification. Do not batch-retire products by SKU assumptions.

Rollback application code under release approval without restoring cleared local pointers, deleting audits, editing allocation rows, resetting counters or replaying jobs. The earlier app already uses Shopify when `local_barcode` is empty, so retired products continue to use Shopify. Retired audit actions remain in history. Re-enabling a retired identifier is not provided; any future recovery requires a separate reviewed operation, never manual database edits.
