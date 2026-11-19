# Phase 4 POS implementation report

Branch: `feature/retail-phase4-pos`, based on main `61098d3` (PR #177). The interrupted working tree was recovered without resetting or replacing its implementation. The draft PR and final commit are recorded in the delivery message.

## Delivered code

| Area | Files |
| --- | --- |
| Canonical integration / POS data | `pos_models.py`, `models.py`, `alembic/versions/20261119_47_pos.py` |
| Decimal pricing / checkout / registers / returns / GL ownership | `pos_money.py`, `pos_service.py` |
| Explicit Shopify 2026-10 client / query-only diagnostic | `pos_shopify.py`, `scripts/validate_pos_readonly.py` |
| Cashier interface / API / Unicode PDF receipts | `pos_routes.py`, `pos_receipts.py`, `templates/pos.html`, `static/pos.js`, `static/pos.css` |
| Navigation / permissions / default-off worker | `main.py`, `retail_worker.py`, `staff_access.py`, `settings.py`, `templates/base.html` |
| Commercial workflow isolation | `sales_order_service.py`, `sales_payment_service.py`, `sales_fulfillment_service.py`, `shopify_draft_order_service.py`, `shopify_order_lifecycle_service.py`, `shopify_payment_sync_service.py` |
| POS / PostgreSQL / browser / permission regression tests | `tests/test_pos.py`, `tests/test_pos_adapter.py`, `tests/test_pos_postgres.py`, `tests/test_pos_browser.py`, `tests/test_staff_access.py`, `tests/fixtures/pos_2026_10_contract.json` |
| Operating / release / source evidence | `docs/retail-phase4-pos.md`, `docs/evidence/pos-2026-10-sources.json`, this report, `README.md` |

The synthetic contract fixture is not live Shopify introspection evidence. Official API documentation evidence is identified separately in the source manifest.

## Verification

All Shopify mutations and WhatsApp sends in tests were mocked. Databases were disposable local SQLite/PostgreSQL; browser requests were intercepted into TestClient. No live store, Railway variables, permissions, scopes or production transactions were changed.

| Run | Result |
| --- | --- |
| Main POS/retail/inventory/commercial sales/payment/Shopify/WhatsApp regression selection | **482 passed, 2 skipped** |
| Final POS service, adapter, query-only diagnostic, permissions, receipt, Chromium and PostgreSQL focused selection | **49 passed** |
| Additional staff access, staff roles, video recording, customer questions, outbound media, delivery locations, scanner/inventory Chromium | **132 passed, 2 skipped** |
| The two engine-specific Draft Order / fulfillment concurrency skips, each rerun separately on UTF-8 PostgreSQL | **1 passed each** |
| Remaining receipt/adapter and PostgreSQL outbound-media selection | **45 passed** |
| Final UTF-8 PostgreSQL POS/retail/barcode/inventory migration, legacy payment/webhook concurrency and outbound-media selection | **20 passed** |

Counts overlap between selections and are not a unique full-repository total. The main selection's two skips depend on the module's primary engine being PostgreSQL, so each was explicitly rerun with a PostgreSQL DATABASE_URL in its own schema. The additional suite's two skips require TEST_POSTGRESQL_URL; they were executed in the dedicated PostgreSQL selection. Existing dependency/deprecation warnings remain; these runs do not claim warning-free execution or an entire-repository test run.

The earlier two failures were legacy PostgreSQL tests running against stale tables lacking the additive `sales_channel` column. They now pass against a fresh disposable schema. The generic section-navigation test now excludes action-level POS grants (as it already excluded retail actions); dedicated tests verify POS view/action combinations, feature disabling and no implicit grants. The new POS concurrency tests separately prove duplicate checkout/worker requests produce one permanent remote attempt and one journal, migration preserves legacy data, and frozen snapshots/tenders/audit cannot be modified.

`git diff --check` and `node --check static/pos.js` pass. Chromium tested desktop, tablet and mobile widths with no POS horizontal overflow, exact scan quantity increments, shared-barcode manual selection and hold/resume. PDFs were checked for 58/80/A4 dimensions, reprint marking and supplied business invoice details.

## Migration and release

Migration: **20261119_47**, parent **20261119_46**. It adds POS tables, nullable SalesOrder channel and immutable financial/audit fences. It does not rewrite Shopify catalog, SKU, barcode, price or stock data. Downgrade intentionally retains financial evidence.

Only after release approval, back up the intended database and apply:

```sh
alembic upgrade head
```

Keep all new gates false until the corresponding acceptance review is complete:

```text
POS_ENABLED=false
POS_CHECKOUT_WRITE_ENABLED=false
POS_REFUND_WRITE_ENABLED=false
```

The complete operator sequence, read-only Railway commands, cashier guide and rollback procedure are in [the release guide](retail-phase4-pos.md). Existing product/inventory write flags and webhook version are unchanged. This PR must not be automatically merged or deployed.

## Outstanding acceptance and practical limits

- Run the supplied credential-safe read-only diagnostic in the existing authorized Railway environment; live shop compatibility was not executed from this workspace. Confirm read_products/read_orders/write_orders/read_inventory/read_locations, optional read_customers, 2026-10 selection sets, actual store location and offline-token eligibility. Do not add credentials to source/chat.
- Complete isolated Shopify development-store acceptance of order creation, manual payment, exact fulfillment/location, inventory decrement, tax, discounts, refund/restock and timeout recovery before production activation. Neither mocked success nor schema introspection proves mutation semantics on the installed app/shop.
- Obtain accounting approval for explicit NONE/EXCLUSIVE tax, account mappings and NET_PRICE_SNAPSHOT discount representation. Shopify receives net line prices and one manual payment transaction; exact original discounts and split tenders remain in the CRM. There is no terminal gateway capture/refund integration or COGS valuation.
- Physically validate HID scanner behavior and 58/80/A4 browser printing. No direct thermal/USB bridge is implemented. Full-store stock reconciliation remains deferred and balances provisional.
- Shopify reads and mutations are not atomic. External stock/order editors can race; query boundaries fail closed, uncertain mutations never automatically retry, and unresolved evidence may require owner/Shopify-support manual reconciliation. No alternate pilot bypass or forced stock override is provided.

Production activation and ordinary real checkout/refund authorization remain separate operator decisions. No live transaction, merge or deployment was performed.
