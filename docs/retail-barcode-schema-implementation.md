# Phase 1B schema-backed barcode adapter — 2026-11-19

## Outcome

Implemented the supplied 2026-10 barcode read/input contract, complete bounded barcode pagination, full-catalog collision scanning, minimal barcode-only mutation preparation/transport, and strict untyped read-back verification. **Production writes remain blocked.** `retail_barcode_contract.MUTATION_SEMANTICS_VERIFIED` is a source-level release gate fixed at **False**, with no environment override. Schema introspection does not certify replacement or custom-value resolver behavior. No live Shopify mutation, deployment, merge, Railway variable change, scope change or webhook-version change occurred.

Branch: `feature/retail-barcode-completion`, based on current main `9f2681e` (PR #169). Existing authentication is reused through `ShopifyCatalogClient`. The Shopify → CRM importer and global API/webhook configuration were not modified. Both product-write flags retain their false defaults and the initial batch default remains five.

## Recovery verification

The interrupted task workspace was recovered on the expected branch. All eleven changed/new files survived: seven tracked modifications and four new files, including the actual schema fixture, completed reader/collision scanner, gated mutation path and tests. No implementation was reconstructed or replaced with older Phase 1B code. Source/test SHA-256 values were checked against a recovery backup before and after validation; the schema fixture retains the exact owner-supplied hash below.

The prior final commit/push had not happened: local history was still `9f2681e` and GitHub had no `feature/retail-barcode-completion` head when checked. The report's test-results placeholder was unfinished. Temporary `/tmp` test logs and PostgreSQL binaries were lost, so a new disposable UTF-8 PostgreSQL test runtime was created and the focused/regression tests were rerun. No production configuration or database was used. Only this report is finalized during recovery; implementation/test files remain byte-for-byte unchanged.

## Supplied compatibility evidence

The owner supplied `pixiepinks_shopify_2026-10_barcode_schema.json`, collected through the existing live Railway connection for `eb2b70.myshopify.com`. Its unmodified contents are retained in `tests/fixtures/shopify_barcode_2026_10_schema.json`.

SHA-256: `ffe2170637cd21f595958bff08b11b802c54d16ddebdf4e46e31359f5a8ffadc`.

The original export predates `write_products` approval. The owner separately confirmed subsequent verification of both `read_products` and `write_products`; no scope grant was inferred from or inserted into the original evidence. Mock transports supply scope responses for tests, and runtime capability checks still inspect actual installed scope handles and shop identity.

Verified structural facts from the evidence:

- `ProductVariant.barcodes` is `ProductVariantBarcodeConnection!`, with `first: Int` and `after: String` pagination arguments.
- `nodes` is `[ProductVariantBarcode!]!`; `pageInfo` is `PageInfo!` with non-null Boolean `hasNextPage` and nullable String `endCursor`.
- Barcode nodes contain non-null String `value` and nullable `BarcodeType`.
- `BarcodeInput.value` is `String!`; `BarcodeInput.type` is nullable with no default.
- Barcode types are UPC, EAN, ISBN, GTIN, ASIN and NS_PID. **There is no CUSTOM enum**. Internal codes omit `type`; no regulated enum or NS_PID classification is invented. Post-update verification additionally requires an explicitly returned null type for the exact code.
- Bulk variant input exposes `id: ID` and `barcodes: [BarcodeInput!]`. Its legacy barcode input is absent. Unrelated fields are optional with no defaults; the strict validator rejects new required or defaulted unrelated inputs instead of silently applying them.
- `productVariantsBulkUpdate` accepts `productId: ID!`, `variants: [ProductVariantsBulkInput!]!` and nullable Boolean `allowPartialUpdates` defaulting to false. The request explicitly sends false. Response IDs and user-error structure are available.

Runtime introspection now includes deprecated output fields and enum values, matching the exporter. This matters for the legacy barcode consistency check. The schema validator verifies the above shapes before the new reader can run. Read compatibility (`read_supported`) is separate from write compatibility (`supported`); the latter remains false under the release gate.

This is owner-supplied schema evidence, not a claim that this execution environment independently contacted Railway. No live product values, complete catalog scan or mutation behavior was observed here. The new read queries must still be exercised read-only in the approved runtime. The catalog root uses the existing importer’s unfiltered `productVariants`/ID pagination approach, isolated at 2026-10; GraphQL/permission errors abort without claiming coverage.

## Complete read and collision behavior

1. Verify configured shop identity, exact served 2026-10 version, installed scope evidence and supported barcode shapes using the existing server-side token client.
2. Enumerate the catalog without a search/status filter, 50 variants per page in ID order. Each result includes its first barcode, parent identity/status and legacy barcode observation. This avoids a request for every ordinary single-barcode variant and keeps query cost bounded.
3. Follow every remaining barcode page, 20 values at a time, for **every** enumerated variant, including archived/draft variants and secondary values. Values/types are preserved exactly; values are never truncated or replaced.
4. Validate every node, explicit type field (missing is not the same as null), page-info fields, cursor progression, parent/variant identity and stable legacy/status observations across pages. Duplicate barcode entries, malformed/truncated responses, unknown types, legacy values absent from the complete collection and missing variants abort the scan.
5. Compare the proposed local value against all observed remote codes. Case and surrounding-whitespace aliases conservatively count as potential collisions and require review. This does not rewrite any identifier or supplier sharing.
6. Re-enumerate all IDs using lightweight 100-ID pages and require the same ordered list. Re-read the selected target’s complete collection and require it to match the scanned target. Match its exact product GID to the approved snapshot.
7. Only after exhaustion and checks return `complete: true` / `collision_complete: true`. All partial/budget/error paths raise controlled errors, never return a truncated “complete” result.

Bounds: configured retail variant cap (at most 100,000), 10,000 barcode values per variant, 200,000 logical read requests and a 60-second catalog-scan deadline checked before/after requests. Errors or limits **block synchronization**. Read retries are bounded to three attempts, respect numeric Retry-After up to 30 seconds, and do not turn an incomplete scan into eligibility. Long/malformed delays require a later retry. GraphQL throttling is handled as a read-only throttle. Response timeouts may overshoot a deadline by the bounded in-flight request, but no overdue result is marked complete.

A scan is not a globally atomic snapshot: another editor can change barcode values between pages or after preflight. The second ID pass detects membership/order changes; it cannot prevent all concurrent value changes. Local locks cannot stop third-party Shopify writers. No remote compare-and-swap guarantee is claimed.

## Minimal update and read-back

The prepared variant input is exactly:

```
{"id": "<approved variant GID>", "barcodes": [{"value": "<unchanged committed P-code>"}]}
```

The enclosing variables contain only `productId` and that one-element variants list. The fixed mutation sets `allowPartialUpdates: false`; it never combines legacy `barcode` and `barcodes`. No SKU, price, inventory, title, option, image, metafield, media or other field is sent. Internal values are validated against the existing uppercase P/base-36 allocation format.

The prepared transport is unreachable in production while the source-level semantics gate is false. Tests temporarily override that gate **only with `httpx.MockTransport`**, and separately enable fixture-only settings. Even after a future reviewed semantics release, direct dispatch requires both existing write flags, valid scope/schema, fresh complete target emptiness, no legacy value and no catalog collision. It rechecks state immediately before the one mutation attempt.

Mutation requests are **never retried** automatically. HTTP failures, timeouts, GraphQL/user errors, malformed payloads and mismatched response IDs require fresh read-back. An accepted response is not VERIFIED. Existing worker behavior performs read-back even after an uncertain response, retaining UNKNOWN or CONFLICT/reservations when the effect cannot be established.

The worker now records complete target values/types and requires the actual adapter’s untyped proof for a matching internal code. A returned UPC/EAN/etc. type cannot become VERIFIED merely because its value equals the local P-code. Empty read-back after uncertainty remains UNKNOWN. Successful read-back verifies the exact value and null type; it does not modify the cached Shopify import snapshot or local immutable assignment.

The worker retains existing shop/claim/version/approval/idempotency checks. The approving user row is also locked across the dispatch boundary so a concurrent grant/account revocation waits for the authorized operation boundary; future queued operations recheck the current permission. PostgreSQL tests cover this fencing. Import/assignment locks and immutable audit evidence remain intact.

## Remaining write blockers

1. **Replacement/custom-value semantics are not verified.** The official mutation documentation still returned proxy HTTP 403 in this task. The supplied schema establishes shapes/defaults, not whether a barcode list replaces/appends, how null type is resolved, or whether a concurrent existing barcode can be preserved safely. The release gate must remain false until authoritative semantics evidence and a reviewed no-overwrite strategy are established. If only unconditional list replacement is available without a conditional/no-overwrite operation, preflight/read-back alone does not prove preservation and the block must remain. Introspection alone must not unlock it.
2. **The new complete reader has not been run against the live store.** Verify query compatibility, all pages, catalog scope/coverage and performance read-only through an approved runtime after code review. Mock tests are not a live scan. Catalogs exceeding the time/read/value budgets deliberately remain blocked; do not bypass them or substitute an unproven legacy search index.
3. **Remote race remains.** Full preflight and read-back cannot supply atomic remote compare-and-swap. An eventual controlled pilot requires paused competing catalog editors and explicit review of the residual risk. No nonempty observed barcode state is eligible.
4. **No real candidate has been selected.** The schema has no live product/variant/code state. Test GIDs and P1 examples are synthetic, not a recommended real update.

The adapter is implemented for supported reads and mock-tested writes, but **not released for production writing**. Environment flags alone cannot change that.

## Tests

Recovery verification rerun: **438 passed, 0 failed, 3 skipped** in **218.86 seconds** in the selected regression suite. All three PostgreSQL-only AI/order cases skipped by the mixed SQLite run were executed separately: **3 passed, 0 failed, 0 skipped** in **4.54 seconds**. None of those skipped cases remains unexecuted.

The focused schema-backed pagination, adapter and approval/recovery run passed **77 tests**, with **0 failed and 0 skipped**, in **42.64 seconds**. These runs overlap and are not summed into an inflated total. The larger suite includes eight retail PostgreSQL migration/concurrency/fencing cases. It includes the two final missing-field guards that were previously checked only in the 25-test focused pagination run; the prior reported larger run was 436 passed. There are no outstanding final automated failures.

New JUnit/log artifacts were saved under `/tmp/barcode-recovery-*.xml` / `.log` and copied to `/workspace/scratch/barcode-completion-recovery/` so a runtime reset does not discard the recovery evidence. Python compilation, JavaScript syntax and `git diff --check` passed. The implementation/test file hashes remained unchanged throughout recovery. This is a selected regression set, not the full repository suite, and no live mutation behavior verification is claimed.

Selected regression coverage includes schema validation, barcode/cursor pagination, secondary and cross-product collisions, many-page targets, missing type/page metadata, duplicates, budgets, unrelated defaults, untyped payload/read-back, real-adapter worker idempotency, timeout/user-error recovery, permission/CSRF, PostgreSQL competing workers/approvals/imports/allocations/revocations, unchanged catalog imports, label decoding, shared supplier selection, staff access/notifications, Inbox/AI and Draft Order/order integrations. All external writes/messages are mocked; SQL uses task-owned SQLite/local PostgreSQL. This is not the complete repository suite.

The first strengthened default-field test selected the explicitly supplied `barcodes` input instead of an omitted price field. That fixture was corrected to test `compareAtPrice`. Its preliminary failure is not counted as a release pass. Final tests below cover the corrected fixture.

## Pilot, migration and deployment

No new migration is required; added remote evidence uses existing JSON job snapshots. Underlying Phase 1B migration `20261118_43` remains required. Neither the Shopify → CRM importer nor global Shopify API/webhook configuration was changed. No deployment, merge, production migration, scopes, variables or write flags were changed.

The [one-variant pilot plan](retail-live-barcode-pilot-plan.md) remains blocked. Before a real pilot, resolve the semantics gate and run read-only integration, then record exactly one real product/variant GID, SKU, immutable local P-code/ledger/version, complete remote empty collection and full collision evidence. Obtain the owner’s separate exact-variant/code authorization, plus any deployment/flag approvals. Only then may the existing approval queue be used for one item. Nothing here authorizes a live mutation.

## Changed files

- `retail_barcode_contract.py`
- `retail_barcode_shopify.py`
- `retail_barcode_sync.py`
- `scripts/verify_live_barcode_connection.py`
- `tests/fixtures/shopify_barcode_2026_10_schema.json`
- `tests/test_shopify_barcode_pagination.py`
- `tests/test_retail_barcode_adapter.py`
- `tests/test_retail_barcode_sync_postgres.py`
- `docs/retail-barcode-schema-implementation.md`
- `docs/retail-management-phase1b.md`
- `docs/retail-live-barcode-pilot-plan.md`

Final delivery provides the actual feature commit and PR/review link.
