# Issue 200 implementation report

Base reviewed: main `9d7e3dd67f2c9b555efbe3f68b84c0fe844bdceb` (PR199). Full GitHub Issue200 reviewed before implementation. Feature branch: `feature/purchasing-goods-receiving`.

## Implementation

* `purchasing_models.py`: shop-scoped bills, directory extensions, payments and receiving records; immutable receiving-to-existing-outbox links; audit records. Existing Supplier and RetailVariant IDs reused.
* `purchasing_service.py`: exact LKR discounts/tax/charges; versioned draft editing and frozen review; admin approval/void; payment balances/history and duplicate reference fences; partial receiving/over-receipt prevention; approved accepted-only movement creation; existing inventory worker preparation, explicit safe attempt0 retry and observation comparison.
* `purchasing_routes.py`, `templates/purchasing.html`, `static/purchasing.js`, `static/purchasing.css`: authenticated/CSRF-protected API and integrated supplier, purchase, receiving and sync screens with imported product search/CDN images, permissions and audits.
* `alembic/versions/20261122_53_purchasing.py`: additive migration, database immutable history guards (SQLite/PostgreSQL); non-destructive rollback.
* `inventory_service.py`: reuse existing delta/CAS dispatcher, preserve all existing non-purchase paths, atomic no-commit preparation option, receipt ownership/gate checks and explicit fresh approval for attempt0 retries. Catalogue version refresh cannot retarget frozen item IDs.
* `settings.py`, `staff_access.py`, `templates/base.html`, `main.py`, `models.py`, `retail_worker.py`: default-OFF gates, granular permissions/navigation/router/model registration, legacy supplier deletion guard and existing worker integration.
* `tests/test_purchasing.py`, `tests/test_purchasing_browser.py`: financial calculation/isolation, partial receiving, duplicates, rejected/damaged stock, gates/auth/CSRF, unknown outcomes, safe retries, crash fencing, source identity, directory/migration integrity and actual Chromium workflow. PostgreSQL races cover repeated receipt approval, concurrent dispatch, competing partial receipts and payments.
* `tests/test_retail_postgres.py`: reconstruct pre-retail migration fixture in correct dependency order including additive purchasing tables.

## Final validation

Only disposable local databases, mocked Shopify and headless Chromium were used. No live queries or mutations were performed.

| Run | Result |
|---|---|
| Purchasing API/service, Chromium UI and PostgreSQL purchasing cases | 39 passed |
| POS/daily/pilot/receipt tests, finance reporting, inventory adapter/service/PostgreSQL/browser, retail import/scanner/PostgreSQL and WhatsApp quick messages | 300 passed |
| Staff access, compact labels, barcode sync/PostgreSQL and hardware integration | 116 passed; one staff PostgreSQL test initially skipped pending its separate local fixture |
| Separate staff PostgreSQL test after provisioning task-owned localhost `staff_access_test` | 1 passed |
| Final distinct test cases across these groups | **456 passed; no unresolved failures or skips** |

Earlier intermediate UI runs found and corrected a zero-payment Decimal formatting bug and test selector ambiguity. Final purchasing suite also covers duplicate delivery/payment references after a lost response with a different client key. Existing dependency/deprecation warnings remain; they are not hidden test failures. `node --check static/purchasing.js`, Python compilation, `git diff --check` and single Alembic head `20261122_53` were checked.

## Release assessment

**Ready for code review; NO-GO for live stock activation pending operator evidence and approval.**

New gates `PURCHASING_ENABLED=false` and `PURCHASE_INVENTORY_SYNC_ENABLED=false` default OFF. Existing `SHOPIFY_INVENTORY_WRITE_ENABLED=false` default is retained. No Railway settings, Shopify data, customer/POS financial records, hardware jobs or production stock were modified. No merge or deployment performed.

Remaining requirements before live activation:

1. Review PR and independently approve deployment/migration53; preserve history and back up database.
2. Validate on an isolated development store: active tracked item, shop/warehouse mappings, required inventory/location scopes, source/schema contract, actual delta/CAS response/read-back, partial receipts and restart/uncertainty behavior. Mock success is not live compatibility evidence.
3. Review every pending/uncertain inventory document and purchase receipt before any broad gate opens, including legacy inventory approvals released by the shared global gate.
4. Run the existing credential-safe read-only inventory diagnostic against each intended item/location with the authorized connection; inspect shop identity and API2026-10 readiness.
5. Obtain separate owner authorization for one precisely scoped real approved receipt and exact positive delta. Only then approve necessary gate changes and observe exactly one adjustment identity/read-back. No automated activation supplied.

Limitations retained intentionally: supplier payments record actual externally made payments in a procurement subledger, not bank/cash/GL postings; supplier payment reversal/returns require separate reviewed workflows. Ambiguous remote outcomes never automatically resend. Present quantity alone cannot prove success or absence of a prior adjustment; manual reconciliation retains Needs Review in purchasing rather than claiming Synced. No guarantee of atomicity across Shopify and PostgreSQL is claimed.

Rollback to pre-purchasing code additionally requires the shared inventory-write gate OFF: old workers cannot recognize the new purchase-specific gate and must not release pending purchase documents. Preserve all new evidence tables.

Full setup, operator steps, acceptance checks, failure handling and rollback: [purchasing-goods-receiving.md](purchasing-goods-receiving.md).
