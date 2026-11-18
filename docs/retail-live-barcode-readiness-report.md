# Phase 1B live Shopify adapter readiness — 2026-11-19

## Outcome and authorization

**Production adapter remains blocked. No live mutation or executable pilot was prepared.** Work started from current main **`48c691d`** (merged PR #167) on **`feature/retail-phase1b-live-readiness`**. The user authorized read-only inspection using the existing live PixiePinks connection, explicitly prohibited Barcode Lab substitution and all live mutations, and required a separately approved exact pilot.

The safe 2026-10 diagnostic hardening from the earlier unmerged adapter branch was reused, with current live-readiness scope observations and a new operational verification command. Mutation code was stopped at the safety boundary. Actual barcode pagination, complete catalog collision checking and a non-destructive custom-code mutation have not been verified or implemented by guessing.

## 1. Actual connection and API evidence

The execution environment has **none** of the following application settings configured:

- `SHOPIFY_SHOP`
- `SHOPIFY_CLIENT_ID`
- `SHOPIFY_CLIENT_SECRET`

Only presence/absence was checked and recorded. No ID, secret or access token was printed, logged or committed. No Shopify/Railway connector tool was available. The existing production application connection could not be obtained from this workspace. No store hostname was guessed, and no Barcode Lab connection was used.

`python -m scripts.verify_live_barcode_connection` reported **`LIVE_CONNECTION_NOT_CONFIGURED`** using the application's existing settings. Its output is recorded in the [machine-readable evidence](evidence/retail-live-barcode-readiness-2026-11-19.json).

| Required live evidence | Result |
| --- | --- |
| Actual live shop identity | Not verified; no authorized connection available |
| Served GraphQL API version | Not observed |
| Explicit barcode diagnostic request version | 2026-10 |
| Existing global API default | 2026-07, unchanged |
| Installed read_products / write_products scopes | Not observed; not claimed absent on the production installation |
| Complete selected-variant barcode collection | Not read or verified |
| Full catalog collision coverage | Not verified |
| Shopify identity matches to actual CRM records | Not verified; no production catalog connection available |
| Real candidate / expected pilot change | None identified or approved |

All four official 2026-10 documentation pages were attempted again using read-only HTTP GET and the inherited network settings. Each failed with **proxy HTTP 403**:

- https://shopify.dev/docs/api/admin-graphql/2026-10/objects/ProductVariant
- https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/ProductVariantsBulkInput
- https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/productVariantsBulkUpdate
- https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/BarcodeInput

The evidence records those denials, not supported-schema compatibility. No official type, pagination, search-coverage or custom input semantics could be confirmed. Synthetic fixtures are explicitly marked and cannot replace that evidence.

## 2. Safe changes completed

- Barcode-only read diagnostics explicitly request API **2026-10** independently of the global API version. Missing or different served-version headers fail closed; no silent version fallback is accepted.
- Read-only introspection observes `ProductVariant`, `ProductVariantsBulkInput`, `BarcodeInput`, the bulk mutation's argument/type metadata, returned shop identity and installed scope handles. Observed type metadata has a fingerprint; it never turns on `supported` or `contract_verified`.
- A configured shop must match Shopify's returned myshopify domain before scope observations are accepted. `read_products` and `write_products` are reported individually as observed handles, not inferred from each other. These metadata observations do not certify complete product access or mutation compatibility.
- Added `scripts/verify_live_barcode_connection.py`. It uses only existing application configuration, accepts no hostname or credential overrides, rejects the named Barcode Lab connection, makes only fixed read-only capability queries, and prints whitelisted evidence without credential values. It cannot inspect arbitrary SQL records, alter flags/scopes/configuration, mutate, choose a pilot or authorize synchronization. Exit 2 means blocked.
- The production `write_empty` method still unconditionally raises. Even correct scopes, introspection and enabled flags cannot bypass the unresolved complete-state/contract checks.
- Blank legacy barcode reads remain explicitly incomplete. No secondary barcode can be silently disregarded; no speculative replacement list or deprecated/new dual input was added.
- Existing approvals, immutable assignments, queue reservations, idempotency, audit history, concurrency fencing, uncertain-result recovery, supplier sharing, explicit variant picker and PDF labels are preserved.
- `SHOPIFY_PRODUCT_WRITE_ENABLED=false`, `RETAIL_BARCODE_SYNC_ENABLED=false` and initial batch default **5** remain unchanged. No production products, prices, SKUs, inventory, orders, credentials, scopes, Railway variables or database records were changed.

## 3. Collision-check approach and limitations

No real complete-catalog collision check was performed. The adapter remains unsupported for this reason as well as incomplete barcode inspection and unverified mutation semantics. Local database uniqueness or legacy Shopify barcode search is not treated as proof about every remote secondary code.

Before completing an adapter, independently verify either a documented full-coverage Shopify search or paginate **every remote variant and every barcode collection**, validating cursors/page boundaries/identities and stopping on incomplete reads, budgets, access errors or unknown state. Confirm the selected code is not used by another remote variant. Do not assume `barcode:` search covers secondary barcode slots. Recheck the exact target immediately before mutation, retain post-update full read-back and document the remaining third-party race; do not claim remote compare-and-swap.

## 4. Automated tests

Final selected regression run: **400 passed, 0 failed, 3 skipped** in **214.67 seconds**. The three skipped PostgreSQL-only AI/order cases were run separately: **3 passed, 0 failed, 0 skipped** in **3.87 seconds**. Those three cases therefore do not remain unexecuted. Tests requiring an actual live connection were not performed and are not represented as passing fixtures.

The earlier focused readiness/adapter/workflow run passed **55 tests**; the additional explicit scope-observation test was included in the final full selected run. These overlapping results are not summed into an inflated test total. The mixed run includes seven retail PostgreSQL tests and 21 readiness/adapter diagnostic cases. No final automated failures remain.

JUnit evidence: `/tmp/live-barcode-regressions.xml`, `/tmp/live-barcode-extra-pg.xml`, `/tmp/live-barcode-unit.xml`. Python compilation, JavaScript syntax and `git diff --check` passed. The real local readiness command exited **2** as intended for `LIVE_CONNECTION_NOT_CONFIGURED`; this is a blocked-readiness result, not an automated test failure.

The selected regression command includes:

```
python -m pytest \
  tests/test_live_barcode_readiness.py tests/test_retail_barcode_adapter.py \
  tests/test_retail_barcode_sync.py tests/test_retail_barcode_sync_postgres.py \
  tests/test_retail_postgres.py tests/test_retail_phase1.py \
  tests/test_retail_product_filters.py tests/test_compact_sku_price_label.py \
  tests/test_staff_access.py tests/test_shopify_catalog.py \
  tests/test_shopify_order_lifecycle.py tests/test_shopify_draft_order_sync.py \
  tests/test_staff_notifications.py tests/test_whatsapp_quick_messages.py \
  tests/test_crm_whatsapp_inbox.py tests/test_global_ai_control.py \
  -q --disable-warnings --tb=short
```

All mutation tests use fixture transports. PostgreSQL is a task-owned localhost instance with disposable schemas; other tests use isolated SQLite databases. No automated test contacts the live shop or runs a production migration. Coverage includes scope/version/identity rejection, no credential disclosure, Barcode Lab rejection, no invented pilot, full-state refusal, secondary barcode protection, duplicate barcode versus SKU distinctions, permission/CSRF, idempotency, read-back, uncertainty/recovery, two-worker/approval/allocation fencing, catalog imports, labels, shared-code selection and existing CRM/AI/order integrations. No full repository suite or verified official-schema/live integration coverage is claimed.

One pre-existing fixture's literal `mutation` substring assertion was updated to check that the operation starts with `query`: read-only introspection legitimately names a mutation type/alias. This does not execute a mutation. The reused fixture also supplies the matching returned shop identity required by the new guard.

## 5. Recommended exact single-variant pilot

**No real variant is recommended yet.** There is no live read-only evidence proving a particular existing variant has an empty complete barcode collection, a committed unambiguous local code and no catalog collision. Supplying a fixture ID or example code would invent that evidence.

The [blocked single-variant pilot plan](retail-live-barcode-pilot-plan.md) lists every field needed for the exact change and the sequence for owner authorization. The barcode state is recorded as **not observed**, not as an empty collection. No flags are enabled and no pilot execution is authorized by this report.

After the existing live connection and official contract are available, perform read-only evidence gathering first, identify exactly one existing eligible variant, record its real IDs/code/state, and obtain the user's separate authorization naming that variant and code. Only a separately reviewed compatible adapter and approved deployment/flag process may then dispatch the existing one-item approval queue.

## 6. Remaining blockers, migration and deployment

1. Make the existing authorized live application connection available to this execution environment through approved secure bindings. Do not guess the hostname, substitute Barcode Lab credentials or paste secrets into chat. No scope or Railway change was requested/executed by this task.
2. Make the official 2026-10 contract/documentation or an independently verifiable supported schema available. Read-only live inspection can then verify actual scopes/schema, sample IDs, complete barcode collections and collision coverage.
3. Complete only the verified minimal custom/untyped `productVariantsBulkUpdate` path; never send both legacy `barcode` and new `barcodes`, overwrite an existing slot, or modify other product fields. Repeat mocked mutation/recovery and PostgreSQL/CRM tests against authoritative fixtures.
4. Prepare the exact one-variant evidence record and request separate authorization before any live change. No development-store substitution is required by this live-readiness task; all mutation tests performed here are mocked.
5. Review/merge before any approved deployment. No new migration is needed; existing Phase 1B revision **20261118_43** is still required by the underlying workflow. No production migration, deployment or flags/scopes change was performed. Retain existing nondestructive rollback, pinned reservations and read-only UNKNOWN reconciliation.

The adapter is **still blocked**, not safely enabled for production synchronization. Protecting existing identifiers and business data takes precedence over claiming completion without the required evidence.

## 7. Changed files and delivery

- `retail_barcode_shopify.py`
- `scripts/verify_live_barcode_connection.py`
- `tests/test_live_barcode_readiness.py`
- `tests/test_retail_barcode_adapter.py`
- `tests/test_retail_barcode_sync.py`
- `docs/evidence/retail-live-barcode-readiness-2026-11-19.json`
- `docs/retail-live-barcode-readiness-report.md`
- `docs/retail-live-barcode-pilot-plan.md`
- `docs/retail-management-phase1b.md`

The final delivery supplies the actual feature commit and PR/review URL. Nothing was deployed.
