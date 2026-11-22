# Issue 200 — Suppliers, purchasing and approved receiving

## Release boundary

This module extends the existing Supplier directory and imported RetailVariant catalogue. Shopify remains the product, image and stock authority. No products, SKUs, barcodes, selling prices, POS transactions, Shopify orders or customer ledgers are created/changed by purchasing. Supplier payments are an **operational purchase subledger**: recording an actual payment does not send funds, affect a cash register or create accounting journals. Bank/GL settlement integration is outside this release; do not enter a payment here expecting money to move.

Migration `20261122_53` follows `20261122_52`. New tables: `purchase_records` (shop-scoped directory extensions, bills, receipts and payments), `purchase_movements` (immutable approved receiving lines linked to the existing inventory outbox), `purchase_audits` (append-only). Existing Supplier IDs are reused. Foreign keys preserve suppliers with history; legacy supplier deletion rejects those suppliers. No historical data is backfilled or altered. Migration creates SQLite/PostgreSQL history guards and checks; it is safe to execute twice. Downgrade intentionally retains evidence.

## UI and access

Stock navigation → **Suppliers & Purchases**, `/crm/retail/purchases`:

* Suppliers: create/update contact, phone, email, address and credit terms; archive instead of deleting; view audit. Profile updates require the displayed version (edit button fills it).
* Purchases/New purchase: search imported title/SKU/barcode; select existing variants with Shopify CDN image; enter ordered quantities, LKR purchase costs, fixed line discounts, fixed/percentage invoice discount, NONE/EXCLUSIVE/INCLUSIVE line tax, charges and charge tax. Calculations use Decimal/cents with deterministic header-discount allocation. Credit terms set due date. Costs never change catalogue selling prices.
* Drafts can be edited with optimistic version checks; supplier/invoice identity stays fixed. Submit freezes the snapshot. Administrator approval does **not** receive stock. Void is allowed only before dependent receipt/payment records exist. Keep an incorrect draft voided with its original invoice evidence; do not silently reuse its identity.
* On an approved bill choose Receive goods. Enter a **unique delivery note/receiving reference** per actual delivery, actual destination and accepted/damaged/rejected quantities. Save → submit → administrator approve. Multiple partial deliveries are supported. All delivered units consume ordered quantity; damaged/rejected units are not sellable and create no Shopify delta. Supplier replacements/returns require separately reviewed documents, not reclassifying these immutable units.
* Record payment: confirm genuinely paid amount, method/date and external reference. Partial/paid/balance and payment/receipt history appear in bill details. No overpayments or unverified payments. A bill/method/payment-reference uniqueness fence also prevents recording the same payment again with a different request key. Corrections require a future separately audited reversal workflow; do not delete payments.
* Sync status: per accepted receipt line Pending, Synced, Failed or Needs Review, attempts, error and Shopify adjustment reference. Details show immutable source/audit evidence. Comparison reads current Shopify vs last stored observation. Differences can legitimately reflect sales, refunds or transfers and do not trigger corrections.

Grants: `retail.purchases.view`, `.prepare`, `.pay`, `.approve`. Active authenticated users and existing CSRF protection required; all reads enforce view. Administrator role is additionally required for directory/location configuration, bill/receipt approval, void and retry. Preparing users also need view for screens. Stock authorization and worker reuse existing inventory prepare/admin checks; approved receipt outbox creator is the approving administrator. No new authentication or credentials.

## One receiving path, durable outcomes

1. Bill save/approval records purchasing intent only; inventory remains unchanged.
2. Approved receipt takes the existing shop transaction lock, validates remaining ordered quantities and exact imported Shopify identities, writes one immutable movement per receipt line, and one DRAFT InventoryDocument per **accepted positive** line. Movement/event and receipt approval commit atomically. Repeated approval returns the same record. Stable request keys reject changed payloads. Supplier/invoice and bill/delivery identities additionally prevent duplication after lost responses/new request keys. Database uniqueness and PostgreSQL shop lock prevent concurrent duplicates/over-receiving/overpayment.
3. Only when all purchase/inventory gates permit, the existing worker prepares and approves each linked inventory document atomically using the original receipt approver's current permissions and active mapped location. Per-line InventoryDocument has its original durable dispatch UUID, supplier invoice, purchase/receipt references, immutable positive delta and audit history. It does not create a second stock ledger or second Shopify dispatcher.
4. Existing worker rechecks scopes/schema, current item/location, approvals and fresh stock, then uses the existing **2026-10 inventoryAdjustQuantities delta/CAS**, `received` reason, idempotency key, reference and ledger URI. Current Shopify stock is read immediately before dispatch; concurrent sales are not overwritten with invoice quantities. A stale CAS/error is treated conservatively.
5. Intent/attempt=1 commits **before** the network call. Success requires validated adjustment-group identity/output and read-back. No automatic second mutation attempt occurs. If the process dies after sending, an exception occurs, or read-back cannot confirm, the job is Needs Review and the existing shop inventory uncertainty fence remains. Later imports/webhooks/POS sales/refunds do not recreate receipt movements or echo inventory writes. Stock comparison is observational, never a second authority.
6. Pre-dispatch failures (attempt=0) show Failed. Explicit admin retry is permitted; unplanned preparation can retry. A frozen planned approval may be renewed only by its original administrator, preserving payload/plan/hash/key. Dispatch still revalidates current stock and scopes. Attempted jobs cannot retry, even after an error or timeout.

This provides exactly one local receipt movement and **at most one application-level mutation attempt**, not a claim of distributed atomicity or guaranteed eventual synchronization. Unknown remote outcomes require authoritative investigation. Matching present quantities alone does not prove application, especially with concurrent sales. Existing Inventory Management reconciliation records observations and a manual closure without resend; it does **not** set Synced/VERIFIED. Purchasing continues to show Needs Review after manual closure. Do not clear an uncertainty fence until adjustment identity/Shopify evidence and business outcome have been separately reviewed. This release does not invent an unverified adjustment-group lookup API or automatically resend a missing result.

## Setup and staged activation (owner approval required)

No production configuration is changed by installation. New defaults:

```
PURCHASING_ENABLED=false
PURCHASE_INVENTORY_SYNC_ENABLED=false
```

Existing `RETAIL_ENABLED`, `INVENTORY_ENABLED`, `SHOPIFY_SHOP` and the existing authorized Shopify client are reused. `SHOPIFY_INVENTORY_WRITE_ENABLED` remains the independent global stock write gate. Do not change existing barcode, POS, printer, Draft Order, webhook or import flags. In particular, enabling a global stock gate can also release independently approved legacy inventory documents: inspect **all** pending inventory documents first.

1. Review/approve PR. Back up the database; inspect migration head and pending inventory jobs. Deploy the reviewed commit only after separate release approval; apply `alembic upgrade head` via the existing release process. Verify `20261122_53` and retained history. Keep new gates OFF.
2. In a disposable development store/database enable Retail/Inventory/Purchasing UI only, keeping purchase sync and global stock writes OFF. Assign grants. Reuse/import products. Configure actual active Shopify locations as SHOP or WAREHOUSE; do not invent location IDs or duplicate the location master. Confirm tracked inventory item is stocked at each destination. Receipt destination may differ from bill default; the approved receipt freezes the actual location.
3. Dry-run with both write gates OFF: bill20 with stock5 → still5; approved receipt20 → one pending +20 movement, still Shopify5. Inspect supplier invoice, delivery note, quantities, rejected/damaged units, identities, approval actor and outbox. Check browser retries/refresh do not duplicate records. Do not use real production deliveries as disposable examples.
4. Authorized operator runs existing credential-safe **read-only** diagnostic in the target runtime (no key/token output):

   ```sh
   python -m scripts.validate_inventory_readonly --include-contract
   python -m scripts.validate_inventory_readonly --variant-id <existing-CRM-variant-ID> --location gid://shopify/Location/<actual-location-ID> --require-write-ready
   ```

   Review shop identity, actual API version2026-10, complete locations, read_inventory/read_locations/write_inventory, source and mutation schema readiness, tracked item and quantities. Do not add scopes automatically. Existing app permissions must be separately approved if missing. No live verification has been performed during implementation.
5. Mocked tests are insufficient for production release. Perform a separately authorized development-store delta/read-back/restart/ambiguity acceptance test first, including a concurrent sale and partial8+12. Review all active inventory uncertainty fences, pending docs, mappings and current permissions. No broad physical reconciliation is implied.
6. Obtain explicit owner authorization for **one real approved receipt at one exact item/location**, stating delta and current stock. Check **all** purchase receipts awaiting sync: these are broad gates, not a one-item pilot subsystem. Keep other receipt work unapproved or sync gate OFF until intended scope is reviewed. Then separately authorize `PURCHASE_INVENTORY_SYNC_ENABLED=true` and (only if required) `SHOPIFY_INVENTORY_WRITE_ENABLED=true`. Ordinary inventory approvals may also dispatch when global gate opens; do not enable it with unreviewed legacy jobs. UI-only `PURCHASING_ENABLED=true` needs its own rollout approval. No automatic activation.
7. Verify exactly one dispatch intent, adjustment group, accepted delta and read-back. Observe shop vs warehouse separation and normal order decrement/refund behavior. Review monitoring/audit and approve wider receiving only after evidence passes.

## Failure, rollback and stop conditions

* Missing scopes/schema, inactive/untracked/missing items, wrong shop/location, revoked approval, changed identity, duplicate invoice/delivery, over-receipt, overpayment or stale version: stop and correct the source/configuration; never edit immutable evidence.
* Catalogue refresh may change descriptive fields/selling price/version. Purchasing retains its cost snapshot and exact product/variant/inventory item IDs. Refresh alone is allowed; any changed inventory identity fails closed. Other inventory workflows retain their existing strict version checks.
* Offline/throttled reads fail preparation/dispatch without mutation; operator may retry attempt0 only. Any transport failure during mutation → Needs Review even if the server may have rejected it. No blind automatic retry.
* Crash with DISPATCHING intent: existing stale-worker reconciliation fences it as UNCERTAIN. Do not reset attempts, keys, statuses or database rows. Preserve reference and search authoritative Shopify adjustment evidence. A read-back mismatch may be a later sale, not a lost receipt.
* Roll back by separately authorized disabling `PURCHASE_INVENTORY_SYNC_ENABLED` first; keep an in-flight attempt under review (disabling cannot undo a request already sent). Disable `PURCHASING_ENABLED` to hide UI. **Before restoring an older application that does not understand the purchase gate, separately authorize setting the shared `SHOPIFY_INVENTORY_WRITE_ENABLED=false` and stop/review in-flight inventory work.** Older workers could otherwise dispatch already APPROVED purchase outbox documents despite the new purchase gate being OFF. Keep the shared stock gate OFF for the entire old-code rollback; this pauses other inventory adjustments too. Never restore that gate on old code while approved purchase documents remain. Preserve all new tables/triggers and supplier/payment/movement evidence; no destructive downgrade. **Never reverse stock automatically** during rollback or replay accepted receipts.
* Bills with dependent receipts/payments cannot be voided; approved receipts with movements cannot be independently rejected/voided. Corrections/returns and supplier-payment reversals require separate review, not deletion or stock reset.

## Automated validation

Tests use disposable SQLite/PostgreSQL schemas and fake Shopify; Chromium exercises the actual API/UI. No live mutations or financial data changes. See `docs/purchasing-implementation-report.md` for exact final results. Core suites: `tests/test_purchasing.py`, `tests/test_purchasing_browser.py`, existing inventory, POS/daily/pilot, finance, barcode/import and WhatsApp regressions.
