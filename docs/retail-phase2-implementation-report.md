# Phase 2 implementation report

Branch: `feature/retail-phase2-scanner`, based on main `df19cad` (PR #174).

## Changed files

- `retail_service.py`: shared exact barcode matcher, separate name/SKU search, shop/version-bound selected-row lookup.
- `retail_routes.py`: scanner screen and guarded details/inventory/Admin/label handoff endpoints; existing `/api/scan` delegates to the shared matcher.
- `retail_inventory.py`: read-only targeted inventory reader, fixed queries, runtime contract/access checks, complete bounded pagination and short per-process cache.
- `templates/retail_scanner.html`, `static/retail_scanner.js`, `static/retail_scanner.css`: dedicated responsive scanner UI, manual shared-variant selection, HID endings, duplicate suppression, stale-response cancellation, optional sound and explicit actions.
- `templates/base.html`, `templates/retail.html`: scanner navigation.
- `static/retail.js`: preserve literal barcode input in the existing Product Master scanner; cache version updated in its template.
- `tests/test_retail_scanner.py`, `tests/retail_scanner.test.js`, `tests/test_retail_scanner_browser.py`: offline backend, PostgreSQL, controller and real Chromium checks.
- `docs/retail-phase2-scanner.md`: staff guide, architecture, limits, deployment acceptance and rollback.

Migration: **none**. No new persistent inventory data; existing indexed barcode/SKU columns, selection and audit tables are reused.

## Verification

All Shopify responses are mocked; no live Shopify reads or mutations were performed.

- Focused final scanner suite: **31 passed**, including one PostgreSQL scanner test and the Node keyboard-controller harness (5 subtests). Command: `python -m pytest -q --disable-warnings tests/test_retail_scanner.py tests/test_retail_scanner_browser.py`, with disposable SQLite plus isolated local PostgreSQL schemas.
- Broader regression suite: **316 passed, 1 skipped**. Included the initial 23 scanner tests plus Phase 1 labels, product filters, retail PostgreSQL, barcode adapter/sync/production/pilot, Shopify catalog/Draft Orders, WhatsApp quick messages and customer questions. The skipped Draft Order concurrency test requires the module's primary database to be PostgreSQL; it was then run separately against a dedicated disposable PostgreSQL schema: **1 passed**.
- Additional browser check after extending verified-zero/location/expiry assertions: **1 passed**. Existing browser tests also verify manual duplicate selection, literal leading zeros, Enter/Tab, stale lookup results, safe HTML rendering, price and desktop/390px mobile layout.
- `git diff --check`, Python compilation and Node syntax checks passed. Desktop/mobile screenshots were inspected locally.

Full broader suite command:

```sh
python -m pytest -q --disable-warnings \
 tests/test_retail_scanner.py tests/test_retail_scanner_browser.py \
 tests/test_retail_phase1.py tests/test_retail_product_filters.py tests/test_retail_postgres.py \
 tests/test_retail_barcode_adapter.py tests/test_retail_barcode_sync.py \
 tests/test_retail_barcode_sync_postgres.py tests/test_retail_barcode_production.py \
 tests/test_retail_p1_pilot.py tests/test_shopify_catalog.py \
 tests/test_shopify_draft_order_sync.py tests/test_whatsapp_quick_messages.py \
 tests/test_customer_questions.py
```

## Remaining acceptance

Live inventory schema/scopes/quantity checks and physical Windows/Android HID acceptance remain pending. Runtime inventory verification fails closed. Documentation retrieval from this execution environment was proxy-blocked; mocked fixtures do not establish live compatibility. Inventory pagination/cache is not an atomic stock snapshot or reservation. Camera scanning is optional and not implemented.

No deployment, merge, scope changes, production environment changes or barcode synchronization activation were performed. Phase 1B source/release controls and Shopify imports/Draft Orders/webhooks are unchanged. Deployment and acceptance require separate review.
