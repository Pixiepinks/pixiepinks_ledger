# Phase 1B implementation report — 2026-11-18

## Outcome

Implemented on the existing `feature/retail-phase1b-barcode-sync` branch, starting at specification commit `3fa1d79` (main ancestor `23e3e80`, including the supplier barcode picker).

**The controlled workflow is implemented, but live synchronization is blocked.** The production adapter deliberately cannot mutate Shopify products because a supported complete multiple-barcode read/collision/write contract could not be independently verified. This is the specification's fail-closed boundary, not an assertion that an empty legacy `barcode` field proves eligibility. This change is not ready for a live pilot. The production adapter contains no HTTP product-mutation path; supported-path execution is exercised exclusively with test fixtures. No global Shopify API version, installation scope, production setting or production product was changed. Nothing was deployed.

Public Shopify documentation requests were denied by the environment's restricted network policy (proxy HTTP 403); no production connection/credentials were configured here. Scope/schema probes are fixed read-only operations using the existing server-side token client and actual served API-version header. Introspection alone never enables writes, including a schema containing `barcodes`. The test fixture is an abstract adapter contract, not verified Shopify schema documentation.

## Delivered

- Separate default-off product-write/barcode-sync gates, bounded batch setting and administrator-only-by-default action permission; no flags-triggered automatic sync.
- Selection-based shop/actor-scoped previews with all candidate snapshots/reasons, a ten-minute expiry, fingerprint and exact batch confirmation. Existing local assignment ledger and import version are rechecked.
- Additive durable jobs/items, unique dispatch keys, unique active reservations and append-only audit events. Database triggers freeze reviewed item snapshots. PostgreSQL and SQLite are covered.
- One-item worker claims with leases and claim tokens; locking shared with importer/allocation; fresh remote preflight twice around the committed dispatch intent; permission/scope/version/flag rechecks.
- Read-back-only verification and recovery after uncertain responses or process interruption. No blind resend, destructive rollback or overwrite. Read-only retries back off, are bounded and never authorize a mutation. Unknown/post-dispatch conflicts retain reservations.
- Barcode Sync review/approval/history/export UI, filtered selections reused from Product Master, pagination and formula-safe CSV. Approval responses use private/no-store caching. The existing shared-code chooser and label presets remain intact.
- Separate pending/imported/remote-verified provenance in Product Master, including last remote check/result. A normal read-only import never manufactures VERIFIED evidence.
- Staging/deployment/scope approval, pilot blocker, testing, cancellation, recovery and nondestructive rollback documentation.

## Migration

`20261118_43_retail_barcode_sync.py`, revision **20261118_43**, parent **20261117_42**; single Alembic head.

New tables: `retail_barcode_jobs`, `retail_barcode_items`, `retail_barcode_reservations`, `retail_barcode_events`. Existing product data/schema fields are not rewritten. Upgrade is repeatable. Downgrade retains evidence and reservations; no destructive data deletion is implemented. Test migrations were run only against task-owned disposable databases.

## Validation

Final selected regression run: **379 passed, 0 failed, 3 skipped** in 226.47 seconds. The three skipped PostgreSQL-only cases passed separately (two AI concurrency/control cases and one draft-order claim/retry case), so none of those cases remains unexecuted. This is a selected regression set, not the complete repository suite.

| Check | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| Final selected regression suite | 379 | 0 | 3 |
| Final Phase 1B safety + PostgreSQL focused run | 40 | 0 | 0 |
| Additional PostgreSQL-only Global AI cases | 2 | 0 | 0 |
| PostgreSQL-only draft-order retry case, UTF-8 rerun | 1 | 0 | 0 |
| Final Product Master verified-provenance check | 1 | 0 | 0 |
| Final CRM navigation/review UI and permission checks | 2 | 0 | 0 |

Focused runs overlap the main suite and are not summed into an inflated total. The main run includes seven retail PostgreSQL tests (five new Phase 1B and two existing Phase 1 migration/allocation checks). There are no outstanding final test failures. JavaScript syntax, Python compilation, `git diff --check` and the single Alembic head check also passed. JUnit artifacts were retained under `/tmp/phase1b-release-final.xml`, `/tmp/phase1b-safety-final.xml`, `/tmp/phase1b-extra-pg.xml`, `/tmp/phase1b-draft-pg.xml`, `/tmp/phase1b-provenance.xml` and `/tmp/phase1b-nav.xml`.

The selected regression command includes:

```
python -m pytest \
  tests/test_retail_barcode_sync.py tests/test_retail_barcode_sync_postgres.py \
  tests/test_retail_postgres.py tests/test_retail_phase1.py \
  tests/test_retail_product_filters.py tests/test_compact_sku_price_label.py \
  tests/test_staff_access.py tests/test_shopify_catalog.py \
  tests/test_shopify_order_lifecycle.py tests/test_shopify_draft_order_sync.py \
  tests/test_staff_notifications.py tests/test_whatsapp_quick_messages.py \
  tests/test_crm_whatsapp_inbox.py tests/test_global_ai_control.py \
  -q --disable-warnings --tb=short
```

Environment: disposable SQLite `DATABASE_URL`, localhost PostgreSQL `TEST_POSTGRESQL_URL` with unique test schemas. PostgreSQL server binaries/data were task-owned under `/tmp`; no production database was contacted. All Shopify reads/writes and messaging in tests are mocked.

The three PostgreSQL-only cases skipped by the mixed SQLite run were run separately: two Global AI worker/control concurrency tests with `AUTO_PRODUCTS_TEST_DATABASE_URL` and one draft-order retry/claim test with PostgreSQL `DATABASE_URL` in a separate UTF-8 database. The draft retry initially hit an ASCII-encoding test-database setup error; the UTF-8 rerun passed. It was not a production or code change.

An earlier broad run found an obsolete Inbox script cache-version assertion already inconsistent with the baseline template, and a timing-sensitive order-webhook concurrency expectation. Only those tests were corrected: Inbox checks that the versioned script is loaded; the order test accepts the existing retryable 503 in-flight duplicate response, retries after the owning delivery finishes, and still verifies exactly-once order effects. The isolated unchanged-base order test passed (demonstrating the timing dependence); no order service or Inbox production implementation was changed. Earlier test failures were investigated and are not silently counted as passes.

Phase 1B acceptance coverage includes empty/changed/nonempty/secondary/ambiguous barcode states, barcode reuse, duplicate local code versus duplicate SKU, documented sharing, current scope/flag/owner checks, stale previews/imports/assignments, inactive/missing/wrong identities/shops, CSRF, exact confirmation, expiry, repeat submission, bounded previews/batches, two competing approvals/workers, concurrent local allocation, import fencing, throttling/backoff, crash/uncertain-response recovery, read-back contradictions, API-version changes at the final boundary, recovery with disabled writes and CSV escaping. Chromium exercises review gates, typed confirmation, queuing and verified history; existing retail browser tests cover filtering, shared-code selection and labels. `/tmp/phase1b-review.png` is test-only UI evidence.

No full repository suite, real Shopify schema integration, live mutation or physical printer test is claimed.

## Changed files

- `README.md`
- `alembic/versions/20261118_43_retail_barcode_sync.py`
- `docs/retail-management-phase1b.md`
- `docs/retail-phase1b-implementation-report.md`
- `retail_barcode_shopify.py`
- `retail_barcode_sync.py`
- `retail_models.py`
- `retail_routes.py`
- `retail_service.py`
- `retail_worker.py`
- `settings.py`
- `staff_access.py`
- `static/retail.js`
- `templates/base.html`
- `templates/retail.html`
- `tests/test_crm_whatsapp_inbox.py`
- `tests/test_retail_barcode_sync.py`
- `tests/test_retail_barcode_sync_postgres.py`
- `tests/test_retail_postgres.py`
- `tests/test_shopify_order_lifecycle.py`

## Remaining deployment requirements

1. Resolve the production adapter blocker with independently obtained supported Shopify schema/documentation and complete-slot/collision/non-destructive CUSTOM write fixtures. Review this additional implementation before enabling any writes. Merely enabling both flags is insufficient.
2. Review/merge the branch, back up and apply migration `20261118_43` in staging, then verify deployed web/worker/imports, Inbox/AI, notifications, labels and shared-code selection together. Current code must not run against the pre-43 schema.
3. Independently confirm the actual installed `write_products` scope and served API version. Scope changes, if needed, require owner approval outside this task; no automatic reconfiguration.
4. Obtain separate production migration/deployment and flag approvals only after the adapter/staging checks pass. Keep writes disabled until then.
5. Only then use a separately approved 1–5-item reviewed pilot, initially one item, while other Shopify catalog editors are paused. A preflight/read-back sequence is not remote atomic compare-and-swap; third-party concurrent edits remain a residual risk. Never use a pilot to discover unsupported schema behavior.
6. Retain all approvals, reservations and event history during rollback/recovery. Resolve UNKNOWN by read-only reconciliation, never by blind resend or deleting remote identifiers.

The final delivery supplies the actual commit SHA and PR/review link. See [deployment and recovery guide](retail-management-phase1b.md) for the exact operational steps.
