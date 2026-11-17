# Retail Phase 1 implementation report

## A. Repository

- Base branch: `main`.
- Base commit: `870b6a2a916008d9351ed5f856c0aba508c06751`.
- Feature branch: `feature/retail-phase1`.
- Final commit SHA is provided with the completion message; this report ships in that commit.
- No merge, production deployment, production migration or environment-variable change was performed.

## B. Features and implementation

Implemented five CRM screens: Product Master, Barcode Management, Barcode Label Printing, Product Audit, and Import & Sync History. The UI reuses existing templates, CSS, sessions, CSRF and staff-access checkboxes. Navigation and all endpoints are default-off behind `RETAIL_ENABLED`.

Product variants have immutable internal IDs and shop/variant external identity. Shopify SKU/barcode values remain distinct from local assignments. Search, filtering, details, CSV exports and frozen cross-page selections are server-side/paginated. Audit classifications distinguish missing/invalid/duplicate/shared/conflicting/stale/missing-in-Shopify records, product-versus-variant counts, options and local pending values.

Allocation uses explicit category prefixes, short `P` + base-36 internal barcodes, database-backed sequences, immutable tombstones, previews and confirmation. Stale previews conflict instead of silently changing values. Repeat confirmations are idempotent. Documented barcode alternatives and duplicate sharing/revocation are separately audited. Imported legacy duplicates are not rejected by migration constraints or silently repaired.

The read-only sync stages paginated Shopify data and publishes only after a complete read. Local identifiers survive reconciliation. Failed/abandoned jobs remain visible with sanitized errors. Full imports reconcile missing variants without deletion; incremental imports retain a five-minute overlap. No unattended import schedule is enabled.

Printing uses python-barcode Code 128 and vector fpdf2 PDF output with embedded Noto/HarfBuzz Sinhala/English text. It supports 25×15, 30×15, 40×20, 50×30 and 60×40 mm presets, custom profiles, 203-dpi whole-dot modules, quiet zones, margins, barcode/text dimensions, visibility options, copy overrides, roll/A4 layouts and calibration offsets. Density/gap/sensor settings are recorded for manual driver setup. A future TSPL backend can consume the same label objects without altering barcode allocation.

PDF generation is asynchronous and bounded. It is tied to the reviewed preview's data/settings. Generated/downloaded/user-confirmed physical printing are separate states; reprints retain parent linkage and snapshots. Conflicting or changed scan identities block new downloads/reprints. No raw USB, receipt, TSPL, POS, warehouse movement or retail payment module is implemented.

Tables: `retail_shops`, `retail_variants`, `retail_sync_jobs`, `retail_import_rows`, `retail_counters`, `retail_identifiers`, `retail_previews`, `retail_audits`, `retail_exceptions`, `retail_label_templates`, `retail_print_jobs`.

Backend routes are under `/crm/retail/api`: product list/history; audit/CSV; selections; allocation preview/page/commit; category and alternative approval; sharing/revocation; sync queue/history/error reports; profiles; PDF preview/jobs/download/physical confirmation/reprint. The complete method/path list and operational instructions are in [the rollout guide](retail-management-phase1.md).

## C. Shopify

- Uses the existing Shopify client authentication and adopted API version (2026-07).
- Required scopes: `read_products`, `read_inventory`.
- Fixed GraphQL query: shop currency and productVariants with product/variant identifiers, status, SKU/barcode, vendor/type, options, prices, inventory item/tracking, images, updates and bounded collections.
- Pages contain 50 variants; collections include the first 5 with a completeness marker. Rate-limit/transient retries are bounded and sanitized.
- `SHOPIFY_PRODUCT_WRITE_ENABLED=false` is the default. No product mutation/export endpoint exists, even if this reserved flag is changed. Existing non-retail Shopify integrations remain separate.
- All development/test Shopify responses were mocked. No live Shopify requests or writes occurred.

## D. Verification

Completed runs (counts overlap):

| Run | Passed | Failed | Skipped |
|---|---:|---:|---:|
| Combined retail, PostgreSQL, WhatsApp Quick Messages, staff access, Shopify catalog, Global AI, sales orders, customer questions and notifications | 253 | 0 | 2 |
| Previously skipped PostgreSQL Global AI concurrency checks, explicitly rerun on disposable PostgreSQL | 2 | 0 | 0 |
| Final retail/PostgreSQL focused run after all label/preview/recovery safeguards | 39 | 0 | 0 |

No unresolved automated failure or skipped check remains among these selected suites. This is not a claim that the repository's entire test collection was run. Existing SQLAlchemy/legacy deprecation warnings remain; warnings were not presented as failures.

Commands used with isolated SQLite URLs and disposable PostgreSQL 16:

```sh
python -m pytest tests/test_retail_phase1.py tests/test_retail_postgres.py \
  tests/test_whatsapp_quick_messages.py tests/test_staff_access.py \
  tests/test_shopify_catalog.py tests/test_global_ai_control.py \
  tests/test_sales_orders_phase1.py tests/test_sales_orders_phase2.py \
  tests/test_customer_questions.py tests/test_staff_notifications.py -q

python -m pytest \
  tests/test_global_ai_control.py::test_postgresql_separate_workers_send_concurrently_and_off_waits_for_boundary \
  tests/test_global_ai_control.py::test_postgresql_concurrent_admin_changes_audit_once_and_invalidate_other_worker -q

python -m pytest tests/test_retail_phase1.py tests/test_retail_postgres.py -q
node --check static/retail.js
python -m py_compile retail_models.py retail_service.py retail_sync.py retail_labels.py retail_routes.py retail_worker.py
git diff --check
```

The tests cover real 1,500-variant import and 1,500-label PDF generation; independent ZXing decode of rasterized 203-dpi numeric/alphanumeric labels; exact physical PDF dimensions; English/Sinhala text; complete human-readable barcode/SKU/price values; label-height/width rejection; A4/copies; atomic import failure and rollback; missing/duplicate/archived/deleted variants; local preservation; stale jobs; allocation concurrency/idempotency; append-only migration triggers; category/permission/CSRF/cross-shop checks; CSV formula escaping; sharing and revocation; PDF failure/reprints; stale print-preview rejection; and actual Chromium allocation/profile/preview/history flows at desktop/tablet sizes.

Existing staff tests retain their legacy section checks. The one-grant/one-page parameterization excludes the new action-level retail grants because they intentionally require companion view grants; explicit retail permission-combination and isolation tests cover that behavior.

Sample roll PDFs were generated with test-only `P1`, rasterized at 203 dpi and independently decoded. Sample A4 labels use test `PP-BIC-000123` and Sinhala/English names. No physical printer was connected. [Sample files](samples/README.md).

## E. Deployment

Do not deploy automatically. Review this branch, back up the production database/volume, record the actual PostgreSQL version and current Alembic head, and test on a restored staging copy. PostgreSQL 16 was verified locally; production's actual version was not accessed.

Install requirements and apply additive migration `20261117_42` with `python -m alembic upgrade head`, keeping retail disabled until validated. Preserve existing Railway settings and deployment startup. Configure persistent `RETAIL_LABEL_DIR` (default `/data/retail-labels`), existing Shopify read credentials/scopes, optional label/catalog limits, then enable `RETAIL_ENABLED=true` and grant staff access. Keep product writes disabled. Web and retail-worker code must be deployed together.

Run an explicit full import, inspect audit/CSV without allocation, resolve conflicts/map categories, then preview a small allocation and label batch. Existing Shopify SKU/barcode values remain unchanged throughout.

Rollback: disable retail/restart or roll application code back while retaining the additive schema, counters, tombstones, audits and PDFs. Never reset committed sequences or delete identifier history. Interrupted read/PDF jobs fail after the documented 15-minute stale interval and require explicit retry/reprint. Detailed recovery: [rollout and recovery](retail-management-phase1.md).

## F. Remaining work and limits

- Physically test the purchased DBL 365B, exact Windows driver/firmware, media support/usable width, sensor gap/calibration, density/speed and scanner reliability. Small-label digital decoding is not hardware certification.
- Follow [DBL 365B setup/acceptance](dbl365b-setup.md), including 100% Chrome printing and repeated scanner/feed tests. Density/gap fields do not remotely configure the printer.
- First authorized live read-only import and scope validation remain deployment checks. Full pagination is eventually consistent; repeat full reconciliation for deletion/change detection. Collections are bounded to the first 5 and explicitly flagged when incomplete. There is no unattended schedule.
- Labels that cannot fit an existing identifier at safe 203-dpi dimensions require larger media. Assigned values are not shortened. A4 support is a generic grid, not every commercial pre-cut sheet layout. Fonts target Sinhala/English.
- No software can recall already downloaded or printed labels after a later conflict/revocation; operators must review those copies.
- Phase 2 prerequisites: physical scan sign-off, reconciled variant/identifier master, agreed stock/location model and separately authorized designs for POS, inventory movements, payment/receipt flows or direct TSPL output. Those modules are not part of this change.

## Changed files
- `README.md`
- `alembic/versions/20261117_42_retail_phase1.py`
- `docs/dbl365b-setup.md`
- `docs/retail-management-phase1.md`
- `docs/retail-phase1-implementation-report.md`
- `docs/samples/README.md`
- `docs/samples/code128-a4-test.pdf`
- `docs/samples/dbl365b-25x15-test.pdf`
- `docs/samples/dbl365b-30x15-test.pdf`
- `docs/samples/dbl365b-40x20-test.pdf`
- `docs/samples/dbl365b-50x30-test.pdf`
- `docs/samples/dbl365b-60x40-test.pdf`
- `main.py`
- `models.py`
- `requirements.retail-test.txt`
- `requirements.txt`
- `retail_labels.py`
- `retail_models.py`
- `retail_routes.py`
- `retail_service.py`
- `retail_sync.py`
- `retail_worker.py`
- `settings.py`
- `staff_access.py`
- `static/retail-fonts/LICENSE`
- `static/retail-fonts/NotoSans-Regular.ttf`
- `static/retail-fonts/NotoSansSinhala-Regular.ttf`
- `static/retail.css`
- `static/retail.js`
- `templates/base.html`
- `templates/retail.html`
- `tests/test_retail_phase1.py`
- `tests/test_retail_postgres.py`
- `tests/test_staff_access.py`
