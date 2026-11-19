# Phase 3 implementation and verification report

Base: main `ce070ca` (Phase 2 merged in PR #175). Branch: `feature/retail-phase3-inventory`.

## Delivered code

- `/crm/retail/inventory`: responsive stock overview, exact barcode/manual variant selection, product/SKU/vendor/supplier filtering, Shopify location selection, targeted distinct available/on-hand observations, low/out-of-stock filters, current-page stock export and paginated document history.
- Receiving documents with supplier/invoice/date/location, immutable product identity/version, quantities, base-currency purchase costs, notes and responsible staff; request-key and supplier-invoice duplicate protection.
- Signed reasoned adjustments, count sheets with append-only barcode-assisted observations, cutoff snapshots, per-line variance and administrator-reviewed opening reconciliation preserving history.
- Pending transfers with actual source/destination identity, source availability validation, administrator approval and ordered dispatch/receiving confirmations. No simulated atomic transfer or automatic two-leg mutation.
- Reorder minimum/target/supplier policies with append-only policy history, live observation alerts, suggested replenishment and movement reports. No automatic supplier orders. Valuation explicitly unavailable without sufficient costing policy/data.
- Shared identity/observation/document/audit services for future POS. Existing Shopify order/Draft Order lifecycle remains its own stock-event authority; no new sale deduction listeners or order/webhook/import changes.
- Durable per-shop serialized approvals, frozen payload/baseline/plan/approval, committed dispatch intent, at-most-one attempt, fresh preflight, proposed CAS/idempotent transport, read-back, uncertainty quarantine and evidence-based manual reconciliation.
- Read-only Railway diagnostic; deployment, staff, POS-boundary and rollback/recovery instructions.

## Release status — important limits

**The live mutation integration is not certified or released.** Official Shopify documentation retrieval was blocked by the execution network proxy (`CONNECT tunnel failed, response 403`), and no authorized Railway execution connection was available. The proposed `inventorySetQuantities` input/idempotent/output contract is fixture-tested, not verified live. `inventory_shopify.MUTATION_CONTRACT_VERIFIED=False` plus default-off `SHOPIFY_INVENTORY_WRITE_ENABLED` prevents production mutations. Introspection alone cannot remove that block. The deployment guide lists exact input, directive, compare/quantity interaction, partial-result and adjustment-group correlation evidence needed before release.

Native transfer support is unverified and remains TRANSFER_PENDING. Stock valuation is intentionally unavailable because no complete costing policy/opening costs exist. Full physical reconciliation is deferred until POS completion, without blocking this development or recording real operational documents later.

No live Shopify request or mutation, Railway/environment/scope change, merge or deployment was performed. Existing barcode flags/controls, Shopify imports, Draft Orders, prices, inventory, product attributes and webhook configuration are unchanged. New independent inventory feature/write flags default off.

## Migration

**20261119_45**, parent **20261119_44**. Additive inventory document/event/count observation/reorder policy/policy event tables and PostgreSQL/SQLite immutable history/document identity fences. No stock backfill, product rewrite, existing-row update or destructive downgrade. Downgrade deliberately retains operational/audit records.

## Changed files

New implementation:

- `inventory_models.py`, `inventory_shopify.py`, `inventory_service.py`, `inventory_routes.py`
- `alembic/versions/20261119_45_inventory.py`
- `templates/inventory.html`, `static/inventory.js`, `static/inventory.css`
- `scripts/validate_inventory_readonly.py`

Integration:

- `main.py`: inventory router, preserving existing retail routes.
- `models.py`: additive model registration.
- `retail_worker.py`: independently gated inventory dispatch through existing worker convention.
- `settings.py`: distinct default-off inventory flags only.
- `staff_access.py`: inventory view/prepare/manage grants and inventory-only staff landing.
- `templates/base.html`: permission/feature-gated inventory navigation.

Tests/docs:

- `tests/test_inventory_phase3.py`, `tests/test_inventory_adapter.py`, `tests/test_inventory_postgres.py`, `tests/test_inventory_browser.py`
- `tests/test_retail_postgres.py`: pre-retail migration reconstruction drops new dependent inventory tables before the retail product table, in the disposable test fixture only.
- `docs/inventory-phase3-guide.md`, `docs/inventory-phase3-deployment.md`, this report.

## Test results

Final Phase 3 suite: **53 passed** (including PostgreSQL and Chromium).

```sh
python -m pytest -q --disable-warnings \
 tests/test_inventory_phase3.py tests/test_inventory_adapter.py \
 tests/test_inventory_postgres.py tests/test_inventory_browser.py
```

Full relevant regression run: **384 passed, 1 skipped**. It included the inventory tests available at collection, scanner backend/Node controllers/Chromium, Phase 1 label printing, product filters, retail PostgreSQL, barcode adapter/sync/production/pilot, Shopify catalog/Draft Orders/order lifecycle, WhatsApp quick messages and customer-question automation. The final focused suite additionally includes four later-added approval/contract/idempotency cases; counts overlap and must not be added together.

```sh
python -m pytest -q -rs --disable-warnings \
 tests/test_inventory_phase3.py tests/test_inventory_adapter.py \
 tests/test_inventory_postgres.py tests/test_inventory_browser.py \
 tests/test_retail_scanner.py tests/test_retail_scanner_browser.py \
 tests/test_retail_phase1.py tests/test_retail_product_filters.py tests/test_retail_postgres.py \
 tests/test_retail_barcode_adapter.py tests/test_retail_barcode_sync.py \
 tests/test_retail_barcode_sync_postgres.py tests/test_retail_barcode_production.py \
 tests/test_retail_p1_pilot.py tests/test_shopify_catalog.py \
 tests/test_shopify_draft_order_sync.py tests/test_shopify_order_lifecycle.py \
 tests/test_whatsapp_quick_messages.py tests/test_customer_questions.py
```

The single skipped case, `test_postgresql_two_sessions_only_one_retry_claims`, requires its primary module database to be PostgreSQL. It was then run separately against a dedicated disposable PostgreSQL schema: **1 passed**. PostgreSQL integration suites used localhost `pixiepinks_test` with per-fixture isolated schemas. All other test databases were fresh disposable SQLite files. Shopify reads/mutations were mocked throughout. No final test failures remain. Existing dependency/deprecation warnings remain.

Coverage includes shared exact codes/leading zeros/variant identities via the existing matcher; staff/admin/session/CSRF/shop isolation; immutable SQL fences; receiving invoice/request idempotency; concurrent worker and duplicate-request serialization; available-only order changes versus on-hand CAS; count cutoff conflicts; no blind retry on timeout/worker interruption; accepted-but-unavailable read-back; typed-reference approval and expiry; revoked access/version changes; distinct barcode versus inventory flags; missing/invalid scope/schema/quantities/location/API version/transport errors; pagination; pending transfers; reporting/CSV escaping; diagnostic mutation rejection; and safe responsive UI/normal receipt submission and approval with zero live writes.

Python compilation, Node syntax and `git diff --check` passed. Desktop and 390px mobile screenshots were inspected. Physical HID hardware and live Shopify stock semantics remain acceptance work after reviewed deployment.

## Operator handoff

See [staff guide](inventory-phase3-guide.md) and [deployment/recovery procedure](inventory-phase3-deployment.md). The initial operator command is `python -m scripts.validate_inventory_readonly`; it uses the existing connection and cannot mutate inventory. No arbitrary product/location/quantity transaction should be invented. Once the contract block is resolved, live verification uses normal explicitly submitted, administrator-approved owner transactions, not a special pilot mechanism.
