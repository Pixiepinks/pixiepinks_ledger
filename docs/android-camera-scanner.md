# Android camera product-information scanner

## Scope and architecture

`/crm/retail/scanner` remains the existing staff-authenticated scanner. Camera input calls the same exact barcode lookup and duplicate-variant picker as keyboard/USB/Bluetooth input. Barcode strings are not trimmed, converted to numbers, expanded, padded or substituted. SKU/name search and scanner endings remain separate. Camera results stop capture before lookup; Scan next restarts the camera only when the preceding input used it. Latest-request fences prevent outdated lookup/detail/stock responses replacing newer results.

This page performs product-information GET requests only. It does not create products, sales, payments, labels, inventory movements or accounting records. Its former label-print shortcut is removed to satisfy the read-only scanner requirement; the separately authorized existing label module and legacy label API remain unchanged. Shopify Admin opens only on an explicit staff click. No printer/agent integration is used.

Camera permission requires an HTTPS secure context and a user click. Rear camera is preferred, not forcibly required. Torch is offered only when the selected video track advertises it. Frames live in an ephemeral canvas, never uploaded or persisted. Capture stops on a detection, close, page hiding, navigation, error or cancellation, including late permission grants. Optional sound and camera vibration remain user controlled.

## Decoder dependency and verified limits

Native `BarcodeDetector` is preferred when it advertises Code 128, EAN-13, EAN-8 and UPC-A. Optional native UPC-E is requested only when advertised. Native runtime failure falls back to the local decoder.

Fallback: **@zxing/library 0.21.3**, Apache-2.0, bundled UMD and license in `static/vendor/zxing/`. Source npm archive: `https://registry.npmjs.org/@zxing/library/-/library-0.21.3.tgz`, file `package/umd/index.min.js`. SHA-256 of the bundled JS:

`d7cc8f69dd70bdcf3ac00c9ae572bf2acb9f4132ba379c72df842e4db918652d`

There is no runtime CDN/network dependency and no new Python production dependency. Chromium tests decode independently generated Code 128, EAN-13, EAN-8 and UPC-A images with the actual bundle. UPC-E decoding failed on valid fixtures in this library; fallback UPC-E is deliberately not advertised or enabled. Use a compatible native detector or exact manual/keyboard entry for UPC-E. Native UPC-E pass-through is tested with a mocked detector, not physical hardware.

The application preserves the decoder's exact text. UPC-A and EAN-13 starting with zero can describe physically identical symbols; decoder format classification may therefore differ between browsers. The decoded format/text is displayed. No automatic alternate/normalized lookup occurs. Compare with the printed number and use exact manual entry if necessary. Do not treat ambiguous physical symbols as proof of a particular catalogue identifier.

## Product image and price

The existing import selects the exact variant CDN URL, then the main product CDN URL. This change corrects the existing `image: {url: null}` case so it can fall back to the main image during an ordinary catalogue import. No import runs on scanning. Existing rows are not rewritten by deployment. The model stores one selected URL; an already imported broken URL shows a placeholder rather than fetching or inventing an unstored alternative. A future normal catalogue import applies the corrected selection. Images retain aspect ratio, can be enlarged, and use the existing Shopify CDN URL/browser cache.

Selling and compare-at prices come from the latest successful CRM catalogue import; the timestamp and stale-data warning are displayed. They are **not live Shopify price verification**. No price is inferred when unavailable.

## Inventory truth and location classification

The existing permissioned inventory API accepts optional `details=true`, using the existing explicitly versioned read-only Shopify client, scope checks, complete inventory-level pagination, identity/version checks, budgets and 20-second cache. Default API calls retain their previous available-only query/response behavior. Detailed requests ask explicitly for `available`, `on_hand`, `reserved` and `committed`; absent optional values are unavailable, never derived by subtraction. Invalid/duplicate quantities and duplicate location IDs fail closed. API errors, unavailable scopes and expired checks do not show quantities as current.

The detailed active total sums each verified active location once. Inactive locations remain visible but are excluded. Without authorized location metadata the active total and group totals are unavailable. Grouping reuses enabled purchasing location mappings (`SHOP`/`WAREHOUSE`); existing register locations are the fallback shop classification. Other app-authorized locations are `OTHER`. No name-based guessing, new mappings, or new location permissions are introduced. Missing groups display unavailable, not zero. Existing CRM product-view authorization and Shopify app read scopes govern visibility; the current application has no additional per-staff location ACL to bypass.

“Last successful inventory check” is the returned read-only Shopify check timestamp, with cache age and expiry. This is not a fabricated persistent synchronization timestamp. The existing inventory read service does not maintain a separate historical sync timestamp. Refresh stock is read-only. Availability is not a stock reservation or fulfillment promise.

Before operational stock reliance, verify the detailed quantity query using an authorized read-only connection with existing read_inventory/read_locations access. No new scopes, API-version variables or write flags are required by this change. An unsupported quantity/query fails unavailable; it never falls back to guessed quantities.

## Deployment and rollback

- Review and merge the PR through the normal release process. **No migration** or new runtime environment variables are required; current head remains `20261122_53`.
- Deploy the reviewed commit through the existing release process, then reload the scanner to obtain the new static asset versions. Existing staff product-view permissions remain required. Do not change Shopify/POS/printing flags.
- Test authorized and unauthorized access and exact desktop lookup before Android acceptance. Confirm all scanner application API calls are GET and no commerce/printing request occurs.
- Roll back by redeploying the previous release and reloading clients. There are no new tables, financial records, camera uploads or data backfills to undo. Normal catalogue imports already performed must not be reversed by rewriting product data.

## Test commands

Use a disposable local PostgreSQL database only:

```sh
TEST_POSTGRESQL_URL=postgresql://agent@127.0.0.1:55429/pixiepinks_test python -m pytest -q \
 tests/test_camera_scanner.py tests/test_camera_scanner_browser.py \
 tests/test_retail_scanner.py tests/test_retail_scanner_browser.py \
 tests/test_retail_phase1.py tests/test_retail_postgres.py \
 tests/test_inventory_adapter.py tests/test_inventory_phase3.py tests/test_inventory_postgres.py \
 tests/test_pos_daily.py tests/test_pos_daily_postgres.py
```

Browser tests need Playwright and Chromium (`/usr/bin/chromium`). Generated-barcode fixtures use the existing label-test dependencies zxing-cpp, Pillow and python-barcode. Node runs camera lifecycle tests. No real camera, Shopify store or printer is contacted by automated tests.

## Physical Android acceptance — still required

1. On a real Android phone, sign into HTTPS CRM as authorized staff; open Barcode Scanner. Repeat with unauthorized staff to verify denial.
2. Tap Scan with Camera. Verify permission grant/denial/retry, rear preference, framing and torch only where supported. Switch apps, close the dialog and navigate away; confirm Android releases the camera indicator.
3. Scan known original Code 128/EAN-13/EAN-8/UPC-A and local P identifiers. Compare the exact displayed text (including leading zeros) with the label and existing lookup. Test UPC-E only if native support is available; otherwise verify manual fallback. Record phone/Chrome version and detector used.
4. Check exact variant image/name/SKU/imported price; test duplicate barcode selection, missing/broken images, image enlargement and not-found/network retry. Verify Scan next captures once, without repeated requests or sound.
5. Compare live read-only stock with the authorized Shopify locations: shop, warehouse, inactive and other. Verify absent reserved/on-hand values show unavailable and expired stock no longer appears current.
6. Repeat desktop manual, USB and Bluetooth input, endings and sound controls. Confirm no sale, stock movement, barcode allocation, label, payment or journal was created.

Automated synthetic streams and rendered fixtures do **not** establish physical Android acceptance. No physical camera or printer validation is claimed.

## Implementation verification (23 November 2026)

- Focused camera/actual fallback decoder/mobile Chromium/read-only API/desktop scanner: **45 passed**.
- Retail, inventory, POS daily, barcode labels/retirement, purchasing and finance regressions: **297 passed**, including **8 disposable PostgreSQL** migration/concurrency tests.
- WhatsApp quick-message/conversation-details regressions: **46 passed**; the PostgreSQL-specific details test was initially skipped because its separate invocation omitted the test URL, then rerun with the disposable URL: **1 passed**, leaving no unresolved skips.
- Existing deprecation warnings remain; no production connection or physical hardware was exercised.

These are test-case counts for separate invocations, not a claim of a full repository test run. Physical Android camera acceptance and an authorized live detailed-inventory read remain outstanding.
