# Phase 2 — Barcode Scanner & Product Lookup

## Staff guide

Open **Retail Management → Barcode Scanner** (`/crm/retail/scanner`). Access requires an active CRM session, Retail Management enabled, and `retail.products.view`. Connect a USB or Bluetooth **HID keyboard** scanner; no vendor SDK is used. Tap the large input before scanning.

1. Choose the scanner's configured ending: Enter (default), Tab, or No suffix. No-suffix mode submits barcode input after 180 ms idle; use Enter/manual Search if your scanner pauses between characters. It does not auto-submit name/SKU searches.
2. Scan or type a barcode and press Search/Enter. Literal leading zeros are preserved. Repeated identical submissions within 600 ms are suppressed. A different barcode immediately starts a new lookup. Clear / Scan next resets duplicate suppression.
3. When a barcode is shared, choose the exact colour/size. Every match remains available through pagination; even a last page containing one row requires manual selection when the total is greater than one.
4. Read the selected variant, exact imported price/currency, identifiers, options, status, and Product details/import timestamps. Barcode lookup never falls back to SKU. Choose the separate SKU/name mode when needed.
5. Stock loads separately. `available` is summed across all returned inventory levels, including any inactive locations, with accessible location names shown. This is neither `on_hand` nor a reservation, sellability or fulfillment guarantee. Missing access, errors, incomplete pagination and untracked inventory show **Stock quantity unavailable**, never an invented zero. Verified zero is displayed only after complete reads.
6. Refresh stock after expiry. Successful/untracked reads cache for 20 seconds per app/shop/variant/version, failures for 5 seconds. The timestamp and cache age are shown; the UI expires old quantities. Refresh can return an unexpired cache entry. No stale positive result is used as an error fallback.
7. Print label explicitly opens the existing label selection workflow; preview and printing remain separate actions. Print permission, current version, effective identifier, missing/inactive/conflict and stale-catalog checks are revalidated on handoff. No new barcode is allocated. Open in Shopify Admin also revalidates permission and identity.
8. Use Scan next to clear the result and focus the next scan. Focus is not stolen by asynchronous responses. Optional sound starts only after selecting Enable scan sound; uncheck to mute. Camera scanning is not included; HID and manual input work independently.

## Read-only inventory contract and limits

`retail_inventory.InventoryClient` inherits `ShopifyCatalogClient` authentication and token handling, using the existing app/shop settings. It uses an explicit **2026-10** endpoint; the global API and webhook versions are unchanged. Only two fixed GraphQL **queries** are permitted: a schema/scope/shop probe and a targeted selected-variant inventory query. There is no mutation, catalog scan, persistent stock table or database write in the inventory reader.

Before displaying quantities the reader checks the returned API-version header, `shop.myshopifyDomain`, installed scopes and introspected field/argument shapes. Product read requires `read_products` or `write_products`. Quantities require `read_inventory` or `write_inventory`; location names additionally require `read_locations` or `write_locations`. Do not change installed scopes as part of deploying this patch. Without them, the scanner remains useful for product/price lookup and reports unavailable inventory/location names.

The reader validates variant/product/inventory-item identity, active product state, tracking, every `available` integer, pageInfo/cursors and duplicate inventory levels. It requests 50 levels/page and exhausts pagination, bounded by 20 pages / 1,000 levels and a cooperative 12-second read budget after the capability probe. Each HTTP read has an 8-second timeout (3 seconds connect). Authentication uses the existing client's timeout; cold auth/probe and an in-flight request can exceed the cooperative budget. No automatic retries, throttle sleeps or fallback to another quantity name occur. Rate limits and malformed/partial GraphQL responses fail closed. Cached schema/scope capability expires after 120 seconds; actual target query authorization errors still fail closed immediately. Per-process result cache is bounded to 256 entries; it is not a distributed rate limiter.

References for review:

- https://shopify.dev/docs/api/admin-graphql/2026-10/objects/InventoryItem
- https://shopify.dev/docs/api/admin-graphql/2026-10/objects/InventoryLevel
- https://shopify.dev/docs/api/admin-graphql/2026-10/objects/InventoryQuantity
- https://shopify.dev/docs/api/admin-graphql/2026-10/objects/Location

Automated tests use explicit mocked schema and API response fixtures. Official documentation retrieval from this execution environment was blocked by the network proxy (HTTP 403); no production inventory connection was exercised. Fixtures are **not live compatibility evidence**. Runtime introspection plus successful complete reads are required before live stock is displayed. Pagination cannot provide an atomic inventory snapshot: quantities can change between pages, during cache lifetime or immediately after reading. Reimport changes invalidate product-version cache keys; the endpoint also rechecks local identity after the network operation.

## Deployment and acceptance (separate review required)

No new migration or environment variable is required. Existing Phase 1B flags, release controls, imports, Draft Orders, webhook behavior and label profiles are untouched. Review and merge separately, then use the normal authorized deployment process. Do not enable any barcode write flags for this feature.

After deployment, an authorized operator should:

- Confirm only users with product-view access can open the scanner/API. View-only users must not see/use Print label.
- With the existing connection, select one known tracked variant and compare a complete targeted read and location breakdown with Shopify Admin. Check a verified zero and an untracked variant. Inspect installed inventory/location scopes without copying credentials or altering them. If access/schema verification fails, retain the unavailable state and investigate separately.
- Test P1/P2 and supplier labels, a shared barcode with colour/size choices, leading-zero labels, unknown/invalid input, stale/inactive products, rapid different/repeated scans and network failure. Check exact price/currency against the last import.
- Test actual Windows USB and Android Bluetooth HID hardware with Enter, Tab and configured no-suffix mode; verify field focus, manual input and Android keyboard behavior. Real hardware and the live inventory check remain pending.
- Test an explicitly clicked Print label through existing preview/PDF on the approved printer profile. Verify scanner use alone creates no allocations, orders, print jobs or Shopify writes.

Rollback: revert the Phase 2 code commit and redeploy through the approved process. No schema rollback or product data restoration is needed. Existing product barcode matching remains the same shared exact matcher. Existing selection/audit rows from explicit label handoffs use unchanged Phase 1 tables; retain them as history.

## Automated checks

Focused: `python -m pytest -q tests/test_retail_scanner.py tests/test_retail_scanner_browser.py` (Chromium at `/usr/bin/chromium`; Python Playwright needed for browser checks). `node --test tests/retail_scanner.test.js` is also invoked by pytest. Browser requests are intercepted offline; Shopify is never contacted. Tests cover safe rendering, desktop/390px mobile layout, shared choices, keyboard suffixes, stale lookup responses, authentication/permissions, shop isolation, version checks, inventory pagination/budgets/scopes/errors/cache and label handoff.

Run existing retail/label/Phase 1B, PostgreSQL, Shopify catalog/Draft Order and WhatsApp/customer-question regressions using disposable databases. See the implementation report/PR for the actual recorded counts and commands. PostgreSQL tests require isolated `TEST_POSTGRESQL_URL`; never point it at production.
