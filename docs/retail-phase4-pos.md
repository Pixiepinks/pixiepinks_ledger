# Phase 4 — Point of Sale

## Release status

The dedicated `/crm/pos` workspace includes cart, barcode/variant selection, checkout, verified receipts, registers, reporting and controlled returns. Migration **20261119_47**, parent **20261119_46**, adds POS orchestration and nullable `SalesOrder.sales_channel`; it does not rewrite existing sales, catalog identifiers or stock.

All three new settings default to **false**: `POS_ENABLED`, `POS_CHECKOUT_WRITE_ENABLED`, `POS_REFUND_WRITE_ENABLED`. These are independent of barcode and inventory adjustment flags. No live credentials, scopes, variables, transactions or deployments were changed during development. Live acceptance is outstanding; mocked tests and documentation review cannot establish that an installed app will accept an order and fulfillment.

## Financial and inventory ownership

- Existing `SalesOrder`, `SalesOrderLine` and `SalesPayment` remain the commercial records. POS tables retain drafts, durable requests, immutable snapshots, drawer movements, receipt audit and journal ownership, not a separate inventory balance.
- POS uses explicit **2026-10** `orderCreate`, existing `ShopifyCatalogClient` authentication, manual external payment and explicit store fulfillment. Commercial WhatsApp Draft Orders continue using their existing workflow and global API version. An anonymous sale omits Shopify customer, telephone, email and addresses.
- Exact cash/card/bank splits remain canonical CRM payment records. Shopify receives **one successful manual SALE for the verified total**. This does not claim that an external terminal was processed by Shopify Payments or that split tender records exist remotely.
- Before any tender, the cashier must confirm actual receipt. Cash change and net applied cash are retained separately. A pending bank transfer stays AWAITING_PAYMENT without an order, receipt, stock commitment or reservation. Supervisor verification makes PAYMENT_READY; cashier confirmation queues the order.
- The Shopify order/fulfillment alone owns stock deduction. A refund alone owns its selected restock. There is no POS `inventoryAdjustQuantities` call or local stock ledger.
- Stock must be tracked, active, observable at the register location, and sufficient. Price, taxability, catalog version and live availability are rechecked. No stock override is supported. Physical balances stay **PROVISIONAL** until the deferred full-store count.
- POSPosting owns exactly one balanced existing GL JournalEntry per completed sale, confirmed refund payout or paid-in/out reference. Gross revenue, discount contra-revenue, tax liability and tender assets are explicit. Refunds reverse original gross/discount/tax cents into a returns account and payout asset. Opening float is a counted custody baseline, not newly recognized revenue. Pending cash movements appear in drawer reports before sale recognition. No COGS/stock valuation is invented before costing policy is approved.
- Existing commercial edit/payment/fulfillment/Draft paths reject POS records. `orders/create` delivery never creates another POS sale; the POS worker owns verified linking. Online/imported sales are not counted as POS sales.

## Discounts and tax

The administrator must explicitly select NONE or EXCLUSIVE tax, the rate/title, existing GL accounts, legal/contact information, role discount limits and **NET_PRICE_SNAPSHOT** policy. No tax registration or rate is inferred. Only products Shopify marks taxable receive configured tax.

`orderCreate` cannot represent arbitrary multiple line discounts as required by this cart. Consequently, Shopify line prices are the discounted **net** amounts, with original price, allocated discount and tax retained as line properties and frozen CRM/receipt data. Shopify-native discount analytics will show zero. This policy must be accepted by the owner/accountant before activation. Do not use this mode if statutory/financial requirements demand native Shopify discount attribution.

The CRM allocates discount/tax cents deterministically per original unit. At most three net/tax segments per variant (20 variants maximum) allow exact partial returns. Original variant selling prices in Shopify are never modified. Above-limit discounts require an active supervisor's approval bound to the current quote hash; edits invalidate approval. Free/zero-total orders are blocked pending a separate approved policy.

## Official API evidence

Reviewed official 2026-10 documentation and content hashes: [source manifest](evidence/pos-2026-10-sources.json). The test introspection JSON is an explicitly synthetic documented contract fixture, not live schema evidence.

- [orderCreate](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/orderCreate): requires `write_orders` and an **offline token**. Direct creation was chosen because existing commercial drafts require delivery details and do not complete anonymous orders. `OrderCreateOrderInput` supports lineItems, transactions, fulfillment, sourceIdentifier and optional customer association.
- [OrderCreateOptionsInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/OrderCreateOptionsInput): default inventory behavior is BYPASS. The adapter explicitly submits **DECREMENT_OBEYING_POLICY**; both notification options are false.
- [OrderCreateFulfillmentInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/OrderCreateFulfillmentInput): required `locationId` fulfills all submitted lines from the selected register's actual Shopify location. No later CRM fulfillment/inventory adjustment is sent.
- [OrderCreateOrderTransactionInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/OrderCreateOrderTransactionInput): `amountSet`, manual gateway, SALE, SUCCESS and locationId represent already accepted external payment.
- [OrderCreateCustomerInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/OrderCreateCustomerInput): only `toAssociate:{id}` is used, after administrator read-only verification of an existing customer. There is **no `toUpsert`**, and marketing consent is omitted. Without a binding, purchase history stays local and Shopify receives no customer profile.
- [refundCreate](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/refundCreate): field-level `@idempotent(key:$key)`, permanent operation UUID, original parent SALE, manual REFUND transaction, line quantities, RETURN/NO_RESTOCK and original location. Read-back checks exact refund note/identity, amount, complete line/restock state and successful manual refund transaction/parent.

`orderCreate` has no documented idempotency mechanism used here. One durable operation allows **at most one mutation attempt**, including on timeout or 429. There is no automatic retry of either create or refund. A UUID-based sourceIdentifier and unique tag support read-only reconciliation. Shop-wide POS locking prevents competing POS workers; unrelated external editors are not locked.

Order identity search reads only IDs/correlation, in complete cursor pages (bounded to 200 results; overflow fails closed). Full read-back supports up to 60 line segments, 20 order transactions and one complete store fulfillment; extra fulfillment/transaction boundaries or incomplete pageInfo block verification. Refund line/transaction connections also fail closed if incomplete. Large externally edited orders require manual evidence review, not guessed completion.

## Administrator setup

1. Release review, backup and approved deployment first. Apply `alembic upgrade head` to the intended database, retaining backup and evidence. Do not downgrade/drop POS financial tables.
2. Keep both checkout/refund flags false. Enable only the UI after approved release, then grant explicit pos.* sections via existing Staff & Access. POS view does not imply sell/refund/admin. Cashiers normally receive view, sell, hold, register_open/register_close; discount/reports are explicit. Supervisors need pos.admin plus relevant action permissions.
3. Run the read-only diagnostic below in the authorized Railway runtime. Preserve existing scopes. Required checkout scopes are read_products, read_orders, write_orders, read_inventory and read_locations; existing-customer association additionally needs read_customers. Review any fulfillment access errors on the actual shop. Do not create another app or paste secrets. Existing authentication must produce an offline token; schema introspection cannot prove this.
4. Configure explicit tax/net-price policy and seven existing GL account roles: sales, discount, tax, cash, card clearing, bank, returns. Tender accounts must be distinct assets. Have an accountant verify account types and postings; configure contact/legal details only from real business records.
5. Create a register for **Magammana West — gid://shopify/Location/92690219311**, verified active through Shopify. A register's location is immutable. One active session per register and cashier; close before handover.
6. Test an isolated Shopify development store with real API acceptance before production activation: cash, external card, bank pending/verification, split tender, tax, discount segmentation, customer association, store fulfillment/stock decrement, lost-response recovery and refund/restock. Confirm the shop's inventory policy and prevent concurrent external stock/fulfillment edits during acceptance. No special pilot subsystem is introduced.
7. Obtain release approval for ordinary POS operations. Only then may the operator enable POS_CHECKOUT_WRITE_ENABLED. Enable POS_REFUND_WRITE_ENABLED separately after refund acceptance and payout/reconciliation training. The existing inventory/product write flags remain untouched.
8. Validate actual 58/80/A4 PDF printing at 100% scale, scanner Enter/Tab, receipts, drawer counts and account reconciliation. DBL 365B receipt mode/Windows driver must be configured by the operator; there is no raw USB/ESC-POS bridge.

## Credential-safe read-only verification

Run **inside the existing authorized Railway service**, not a local shell populated by copying secrets:

```sh
python -m scripts.validate_pos_readonly
python -m scripts.validate_pos_readonly --variant-id <EXISTING_CRM_VARIANT_ID> --location gid://shopify/Location/92690219311
```

The script imports no application startup, runs no migrations/create_all, performs no commits, and uses a PostgreSQL READ ONLY transaction for optional catalog selection. Its transport rejects create/refund mutations even if settings are enabled. Output contains adapter hash, scope/schema checks, real read-query validation, locations and optional stock/price observation, never tokens. Keep evidence in restricted release records; product/location identifiers are operational data.

Expected: READ_ONLY_COMPLETE, exact version/shop/currency, all contract checks and needed scopes present, active location, observable tracked product. Null-target READ and empty correlation search validate real selection-set shape without creating an order. This does **not** prove mutation acceptance, offline-token eligibility, tax computation, payment or fulfillment semantics. Those require isolated-store acceptance and later ordinary authorized transaction verification. No live diagnostic was executed from this development workspace.

## Cashier guide

1. Open the selected register with the physically counted float. Refresh/check connection. Scan exact barcodes and Enter (or choose Tab suffix). Leading zeroes stay strings. Shared supplier codes open the existing variant picker: choose colour/size explicitly; SKU search is separate.
2. Build the cart, adjust quantities and line/order fixed or percentage discounts, or hold on the server. Availability in the selected cart is an observation, not a reservation. Resume held sales at the same Shopify location, including after opening a replacement session.
3. Walk-in Customer is the default. Optional name/phone/invoice details affect only the sale. Search/select an existing CRM profile for purchase history. Create a profile only with explicit operational contact consent; marketing remains separate. Admin Setup can bind an already existing Shopify customer ID after verification without creating a duplicate.
4. Confirm actual cash/card payment, reference and cash received. Split tenders must sum exactly to the total; change applies only to cash. Never enter PAN/CVV. Unverified bank transfers need supervisor verification and cashier finalization. Do not hand over goods while QUEUED/AWAITING_PAYMENT/UNCERTAIN/REVIEW.
5. A COMPLETE sale has verified Shopify identity, lines, total/tax, successful manual payment and exact store fulfillment. Open 58/80/A4 PDF for preview/print/save. Reprints are audited and labelled DUPLICATE. A pending/uncertain sale cannot produce a final sales receipt.
6. Sales & returns provides date/cashier/tender filters, bounded product/customer API filters, receipts and Shopify reconciliation states. Summary groups cashier/tender/product, discounts and paid refunds. CSV exports are capped at 1,000 sales and escape spreadsheet formulas. Sale summaries attribute refunds to the original sale date; dated physical payouts are visible in register movements.
7. Supervisors select original items and remaining quantities, payout method, reason and actual return/restock decision. The worker verifies Shopify's refund before AWAITING_PAYOUT. **Confirm actual payout separately**, then print the refund receipt. This confirmation records cash leaving the drawer or the external card/bank refund reference; it does not charge/refund a real card gateway itself.
8. Record paid-in/out with a reason and distinct offset GL account. Close with physical cash count and notes. Differences require supervisor permission. Pending/uncertain checkouts and refund payouts block closing; held carts do not reserve cash or stock. Register reports retain opening, movements, expected, count, difference and closer.

## Recovery and rollback

- Retain the sale/reference and open its status after any network failure. Repeated checkout uses the same permanent cart/request key and must match frozen tenders. Never make a replacement sale to resolve an uncertain checkout.
- Worker intent is committed before I/O and permanently records attempt=1. A DISPATCHING operation interrupted for two minutes becomes UNCERTAIN. Shop dispatch pauses while any DISPATCHING/UNCERTAIN/REVIEW operation exists. Refunds likewise never retry automatically, despite their remote idempotency key.
- Supervisor **Read-only reconciliation (no resend)** looks up exactly one source-correlated order, then verifies it independently. It can discover a refund by exact immutable operation note even if its returned ID was lost, and verifies all line/restock/payment evidence. Accounting finalization uses a savepoint; failed posting/read-back retains evidence and no partial journal.
- Zero matches, duplicate matches, permissions errors, incomplete pagination, external edits, mismatched tax/fulfillment/payment, or missing refund evidence remain blocked. Search indexing can lag. Wait and reconcile; zero results never authorize a second create. Persistent uncertainty needs owner/Shopify-support financial reconciliation. Do not SQL-reset attempts, delete audit, reuse an operation key or make a compensating stock adjustment blindly.
- An unsent checkout may be supervisor-voided only before any remote attempt, after confirming cash returned. Verified noncash external receipts require manual external reversal before treating the commercial void as settled. Never void a remotely attempted sale locally. Completed sales use controlled returns/refunds.
- Rollback: first disable POS checkout/refund dispatch with approved operator settings, then hide UI if necessary. Do not alter Phase1–3 settings. In-flight intent may have succeeded; read-only reconciliation remains necessary. Retain all tables, receipts, canonical sales/payments, journals and attempt fences. Reverting application code is not permission to remove financial data or resume old workers against POS orders.
- Concurrent external Shopify edits remain non-atomic: read/inspect and orderCreate are separate operations. Stock can change between them; DECREMENT_OBEYING_POLICY and read-back reduce risk but cannot guarantee a global reservation. Pause competing integrations for acceptance; monitor normal operations. No claim of race-free catalog/inventory snapshots is made.

## Test procedure

Use a disposable database and mocked Shopify responses only. Python environment uses existing requirements (FPDF Unicode fonts, pypdf and Playwright); no printing hardware is required.

```sh
DATABASE_URL=sqlite:////tmp/pos-tests.sqlite python -m pytest -q tests/test_pos.py tests/test_pos_adapter.py tests/test_pos_browser.py
DATABASE_URL=sqlite:////tmp/pos-pg-bootstrap.sqlite TEST_POSTGRESQL_URL=postgresql+psycopg2://<LOCAL_TEST_USER>@127.0.0.1:<PORT>/pixiepinks_test python -m pytest -q tests/test_pos_postgres.py
node --check static/pos.js
```

The PG fixture refuses non-local databases and creates/drops an isolated schema. Browser tests intercept HTTP into TestClient and mocked services, checking scan increments, ambiguity, hold/resume and desktop/tablet/mobile overflow. Tests cover money allocation, walk-ins/customer links, pending banks, discount approval, no duplicate create/refund/posting, immutable financial records, receipts and independent write flags. Existing retail/inventory/commercial/WhatsApp regressions are included in the implementation report.
