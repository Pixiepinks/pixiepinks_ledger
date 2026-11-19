# Phase 3 Inventory Management — staff and administrator guide

## Authority and release status

Shopify owns all inventory balances. The CRM stores documents, observations, approvals, policies and audit events, not an independent stock ledger. The same product/variant/inventory-item/location identities and exact barcode matcher support Inventory Management and the future POS.

**Development implementation; live write compatibility is blocked.** The proposed compare-and-set/idempotent mutation path is mock-tested but has not been certified against authoritative Shopify 2026-10 documentation or the configured live app. `inventory_shopify.MUTATION_CONTRACT_VERIFIED=False` is a source-level block in addition to a distinct, default-off `SHOPIFY_INVENTORY_WRITE_ENABLED` setting. Changing an environment flag alone cannot release it. No live stock operations were performed during development. This is a normal transaction workflow, not a special one-product pilot.

Native transfer behavior is also unverified: approved transfers stay `TRANSFER_PENDING`, and dispatch/receiving confirmations are records only. They never trigger a pair of simulated inventory adjustments.

## Stock overview

After a reviewed deployment with `RETAIL_ENABLED` and `INVENTORY_ENABLED`, open **Stock operations → Inventory Management**. Staff need `retail.inventory.view`. Search imported product/variant names and SKUs, or use exact barcode search (USB/Bluetooth HID + Enter or manual Find barcode). Leading zeros are preserved. SKU is never used as a barcode fallback. Shared barcode results must be selected explicitly with Add to document; no scan creates a movement.

Choose an actual Shopify location. Refresh a product's stock or refresh the current page (10 products maximum). The response distinguishes:

- **VERIFIED_LIVE**: successfully read Shopify `available` and `on_hand` at the selected location. They are separate quantities and can differ because of reservations, committed orders and other inventory states.
- **PROVISIONAL physical balance**: no completed opening reconciliation has established a physical opening reference. A verified live API observation does not prove a physical count.
- **OPENING_RECONCILED**: there is a verified opening document for this variant/location; its reference is shown. The opening verification is a historical event, not a perpetual guarantee of physical accuracy.
- **UNAVAILABLE**: missing scopes, inactive/untracked/missing product, missing location activation, incomplete response, API/schema/version error or read budget. No zero is substituted. Shopify inventory tracking/activation is never changed automatically.

A genuine zero is shown only after a successful complete targeted read. Observations expire in the UI after 20 seconds; refresh before relying on them. These are not atomic catalog snapshots or stock reservations. A refresh failure discards the displayed quantity rather than restoring an old positive result. Switching location clears observations. The dashboard reads a location on demand, not the entire catalog on every scan.

The supplier filter uses local product/location reorder policies. Shopify vendor filtering is separate. Low-stock and out-of-stock filters apply to observations loaded on the current page; unknown/unavailable rows remain distinct. Stock CSV exports preserve unavailable rows and read timestamps. Movement CSV covers the most recent 500 documents, while document history is paginated. CSV values are escaped against spreadsheet formula execution.

## Stock receiving

`retail.inventory.prepare` allows staff to prepare receiving, adjustments, counts and transfers; grant view permission too for the UI. No stock-changing action is granted by a staff role alone.

1. Select a receiving location and add explicit tracked products from Stock overview (up to 10 unique product/location pairs per document).
2. Open Receiving & adjustments; select Stock receiving.
3. Choose an existing supplier, enter the supplier invoice/reference, date, base-currency unit purchase costs, quantities and notes. The responsible staff username/ID is saved server-side. Costs support two decimal places; multi-currency landed costing is not implemented.
4. Save the document. Input, reference and request/business identity become immutable. The browser retains a request key for an unchanged in-flight retry. The same supplier invoice cannot be recreated under a different key; review the existing receiving document. For separate invoices/deliveries use a meaningful distinct supplier reference. Rejecting a document does not free its invoice key; retain and refer to history when correcting mistakes.
5. Submit explicitly. The CRM captures fresh Shopify on-hand and available observations and freezes the before/after plan. No stock write occurs at save or submit.
6. An active administrator reviews exact product, location, quantity, costs, baseline and reference, types the exact document reference, then approves. Approved transactions remain queued while the write contract/settings are blocked.
7. Once the normal write path is separately reviewed and released, the existing background worker preflights the active approver/creator grants, identity/version, approval integrity, fresh quantities, installed scopes and supported contract. It records durable dispatch intent, makes at most one mutation attempt, and reads back every affected product/location before recording VERIFIED.

Supplier invoice keys prevent duplicate receipt posting. Request idempotency, frozen approvals and a permanent one-attempt dispatch fence protect retries. A failed/uncertain transaction must be reviewed, never recreated just to “try again.”

## Adjustments and returned stock

Prepare a Stock adjustment with a signed quantity. Damaged and Lost must be negative; Returned goods and Correction/Manual may be signed as appropriate. Add a clear reason, date and notes. Review the on-hand variance after submission. Administrator approval is mandatory.

For a return already restocked by a Shopify order/refund process, do not create another restock adjustment. Shopify order/Draft Order lifecycle owns stock changes for those business events. This module does not subscribe to orders or deduct stock again. It does not modify order, Draft Order, fulfillment or webhook integrations.

## Counting and opening reconciliation

Select products/location and choose Physical count sheet. Acknowledging the cutoff means staff will control physical receipts/dispatches during counting and record the procedure in notes. Saving starts the sheet with fresh, immutable Shopify baselines; no correction occurs yet. Scan/add each variant explicitly, record counted quantities in the saved sheet, and retain the observation history. Corrections to observations append a new observation; previous counts are not erased.

Every line needs a count. Submitting freezes the latest counted values and per-line variance. If on-hand or available changed since the cutoff, restart the count with a new sheet. Before dispatch, both are checked again; the proposed mutation uses Shopify on-hand compareQuantity rather than ignoring concurrent changes. A concurrent order that changes available only is not restored to its old available balance by an on-hand correction; an on-hand conflict fails closed. Cross-quantity changes, ABA changes and multi-page reads are not an atomic physical snapshot. The cutoff/movement-control procedure, read-back and operator reconciliation remain essential.

Full-store physical reconciliation is **not required now** and is intentionally deferred until POS completion. The Opening reconciliation type uses the same count/approval workflow later: preserve all receiving, sales, adjustments, transfers and observations; record discrepancies, operator, cutoff, verification time and reference without resetting history. Verified opening references are shown per variant/location. It is not a bulk “reset inventory” button. Reconcile in explicit bounded sheets with reviewed ongoing-movement controls, not an unrestricted full-store overwrite.

## Transfers

Choose a source location/product/positive quantity, a distinct active destination, and a meaningful transfer reference. Submission validates source available stock and destination identity. Administrator approval records `TRANSFER_PENDING`. Record dispatch confirmation before receiving confirmation. These actions **do not move Shopify stock** in this release. They deliberately stay pending until native transfer APIs, lifecycle/concurrency behavior and scopes are verified. Do not compensate with two manual adjustments and call them atomic. Before later native release, review existing physical/Shopify transfers so pending records are never automatically backfilled into stock movements.

## Reorder and costing

`retail.inventory.manage` grants local reorder-policy management. Select a product/location, set minimum/target and supplier. Target must be at least minimum. Live available stock drives low-stock flags and suggested replenishment (`max(0,target-available)`). There is no automatic supplier purchase order. Policy changes have append-only audit history.

Receiving unit costs are recorded. A complete inventory valuation is deliberately unavailable until an approved costing policy, opening costs, returns/discount/freight handling and all required data are established. Receiving costs alone must not be presented as total stock value.

## Administrator recovery

- DRAFT → SUBMITTED → APPROVED → DISPATCHING → VERIFIED is the normal non-transfer lifecycle. A no-change count is verified without a mutation.
- CONFLICT means the preflight rejected stale identity/approval/stock. No remote attempt occurred; reject/review and prepare a newly documented transaction with a distinct business reference as needed.
- UNCERTAIN means a mutation might have been applied or a dispatching worker stopped. There is no automatic retry. Any DISPATCHING, UNCERTAIN or APPLIED_REVIEW document blocks further inventory dispatch for the shop.
- APPLIED_REVIEW means Shopify returned an adjustment group but complete read-back failed or quantities changed afterward. Preserve the remote reference and investigate concurrent activity; do not assume failure and repeat the adjustment.
- The administrator must inspect Shopify inventory history, the document reference/idempotency key, affected variants/locations and intervening order/fulfillment/other adjustments. The Reconcile action performs fresh reads and records required evidence plus typed document confirmation, then marks RECONCILED_MANUALLY. Matching current quantities alone are **not** proof that an uncertain mutation succeeded. This manual status never becomes API-VERIFIED and does not establish a verified physical opening reference.
- An interrupted dispatch older than two minutes becomes UNCERTAIN even if the write flag is off. It is never requeued. There is no reset-attempts or resend endpoint.

References, payload, baselines/plans once set, approver/hash once set, dispatch identity/attempt fence, events and observations have database immutability protections on PostgreSQL and SQLite. Do not edit/delete these records directly, including during rollback.
