# Phase 1B live read-only barcode validation

2026-11-19 (Asia/Colombo). Base: main `c5f88cf` (PR #170).

Follow-up: [20-barcode pagination correction and P1 operator rerun](retail-barcode-pagination-20.md). The historical observations below describe the earlier task; the follow-up records the owner-reported live results and current fix.

## Outcome and evidence boundary

This change adds an operator diagnostic, tests and this procedure. It does not change the production adapter, source-level gate, Shopify imports, Draft Orders, webhook version, database schema, approvals, worker or environment defaults. `MUTATION_SEMANTICS_VERIFIED=False` remains unchanged. Both write flags remain disabled. No live Shopify request, production database query, mutation, deployment or Railway variable change was performed by Codex.

No Railway connector/CLI or authorized runtime session is available here. Local execution returned `BLOCKED / RUNTIME_CONNECTION_UNAVAILABLE`, with `coverage_complete=false`, `candidate=null`, `source_gate_false=true`, `write_flags_disabled=true`. Configuration **names**, not credential values, are reported. Consequently there is no measured live scan duration, verified live catalog coverage or identified live pilot allocation in this report. The operator must execute the procedure below before claiming these results.

## Official semantics reviewed against the implementation

The owner confirms the following official 2026-10 documentation:

- [ProductVariantsBulkInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/ProductVariantsBulkInput): `barcodes` supplies the replacement set of variant barcodes.
- [BarcodeInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/BarcodeInput): omitting `type` stores the submitted value without assigning a regulated barcode standard.
- [Barcode replacement changelog](https://shopify.dev/changelog/posts/product-variant-barcode-is-being-replaced-by-barcodes): the legacy barcode field is being replaced by `barcodes`.

These are documented here as **owner-confirmed official semantics**. Independent retrieval of all three URLs from this execution environment returned proxy `403 Forbidden`; they were not independently downloaded here. The owner-supplied schema fixture establishes field shapes, nullability and pagination metadata, not replacement semantics. Do not confuse schema introspection with this semantic evidence or with authorization to write.

The implemented pure payload builder matches that contract: one `productId`, one variant `id`, and `barcodes: [{value: committed_internal_code}]`, with **no `type` key**. There is no invented `CUSTOM` enum and no UPC/EAN assignment. The fixed mutation sets `allowPartialUpdates:false`. No SKU, price, inventory, title, options, images or deprecated barcode input is submitted. Existing schema/default checks remain fail-closed.

**Replacement is not append.** The adapter must refuse any nonempty complete barcode collection or legacy/collection contradiction. Sending only a new code to a populated variant would remove existing supplier/manufacturer values; this workflow must never do that. The current adapter checks full emptiness and collisions before constructing a possible write, but its source gate still refuses production writes. No semantic gate override, including test overrides, was used during this task.

Even with documented replacement semantics and a successful full scan, the API call is **not compare-and-swap**: another actor can add a target barcode or reuse the selected value between preflight and mutation. Ordered ID re-enumeration catches observed membership changes, and target reread catches observed target changes. Neither detects all non-target barcode edits, edit-and-revert changes, or edits after the last read. This remains a release blocker requiring a separately reviewed operational concurrency policy.

## Reviewed operator command

Use the existing **authorized remote shell inside the running Railway application container**, in its application working directory. Do not use a command that exports Railway variables locally. Do not install an app, paste credentials, alter variables, run migrations, start workers or deploy this branch merely to perform a diagnostic.

If the reviewed script is already present in that runtime:

```sh
python -m scripts.validate_shopify_barcode_readonly
```

This performs the unfiltered accessible-catalog scan **without opening the CRM database**. There is no invented candidate or fabricated barcode. Exit 0 means only the read-only scan completed. Exit 2 means blocked, incomplete or inconclusive. Neither exit code authorizes any update.

If the script is absent (expected on the deployed PR #170 image), use the existing approved remote shell's Python stdin support:

```sh
python - <<'PIXIEPINKS_REVIEWED_READONLY_SOURCE'
# Paste the complete, reviewed contents of scripts/validate_shopify_barcode_readonly.py here.
PIXIEPINKS_REVIEWED_READONLY_SOURCE
```

The placeholder above is **not** the diagnostic: replace it with the exact reviewed file contents before execution. This executes in memory with the existing deployed modules and credentials, without changing files or deploying. Do not paste tokens or environment output. Review the source from this feature commit; the script verifies the deployed adapter's SHA-256 is `180dc2501e3dbb262133ffb682f5c56fa7573830afe25914fef7f7223b7d0f93`. A different adapter blocks with `DEPLOYED_SCANNER_VERSION_MISMATCH`; review that version instead of weakening the check.

To examine a specific **existing** immutable allocation, obtain its `retail_identifiers.id` from an authorized read-only CRM view/operator inspection. Do not substitute the product ID, variant ID or barcode number. Then, if the script is available:

```sh
python -m scripts.validate_shopify_barcode_readonly --allocation-id EXISTING_ALLOCATION_ID
```

For stdin execution use `python - --allocation-id EXISTING_ALLOCATION_ID <<'PIXIEPINKS_REVIEWED_READONLY_SOURCE'`, followed by that same reviewed source and closing delimiter. Replace the placeholder ID with a positive existing integer. No allocation is created or modified. If none can be identified unambiguously, run the no-allocation command and report `candidate=null`.

The optional allocation read uses only raw SELECT statements in a PostgreSQL transaction starting with `SET TRANSACTION READ ONLY` and a five-second local statement timeout, always rolled back. It imports neither application startup nor ORM models/migrations. SQLite is refused. It checks the immutable ledger/current local assignment join, shop, identifiers, active/fresh local row and conservative local duplicate identifiers (including shared SKU cases requiring review). It rereads the same allocation after the remote scan. Ineligible or ambiguous local data produces no candidate; the general catalog scan can still complete. The result is a proposal only, never an approval, queue entry or audit write.

## Transport and coverage safeguards

The diagnostic subclasses the deployed `BarcodeClient`, reusing `ShopifyCatalogClient` authentication and token refresh. A transport whitelist permits only the exact fixed `PROBE`, `CATALOG`, `READ` and `CATALOG_IDS` query strings on `https://eb2b70.myshopify.com/admin/api/2026-10/graphql.json`, plus the existing client-credentials OAuth endpoint. It rejects mutation documents, REST paths, foreign hosts, URL credentials/query strings and alternative request bodies before network dispatch. `write_empty` also unconditionally refuses. No arbitrary GraphQL option is provided.

Source-gate and existing write-flag checks run before and after the scan; the diagnostic never changes them. If already enabled, it refuses rather than silently disabling settings. Shop identity and served 2026-10 version must match. Current read/write scope observations are reported, not granted. Global API/webhook settings remain untouched.

The deployed scanner requests 50 variants per catalog page, first barcode per variant, then up to 20 values per continuation page; every continuation is exhausted before counting a variant as fully examined. It validates node/product identity, explicit nullable type, page metadata, cursor progress, duplicate values and legacy/collection consistency. It then re-enumerates all accessible IDs, ordered by ID, 100/page. With an actual allocation it calls the deployed `inspect` method, scans all secondary values for case/whitespace aliases of that code, and finally rereads the target and compares its complete state.

Scope of coverage is **all variants returned by the unfiltered connection under this app's visibility**. API permissions and app visibility can constrain that catalog. No GraphQL scan proves visibility of inaccessible variants. A scope error, schema mismatch, unavailable variant, invalid page, changing membership, deadline or limit is an incomplete scan, never evidence of emptiness. An empty catalog is inconclusive. No-allocation mode reports `collision_check_for_candidate=false` even after full coverage: it checks no invented code.

## Budgets, throttling and performance interpretation

Existing production limits are preserved:

| Limit | Value / behavior |
|---|---|
| Scan deadline | 60 seconds, checked between logical operations |
| Variant limit | Lower of 100,000 and configured `RETAIL_MAX_VARIANTS` |
| Barcode values per variant | 10,000 maximum |
| Logical read budget | 200,000; HTTP retries can add attempts |
| HTTP timeout | 15 seconds, connect 5 seconds |
| Retries | At most three attempts for existing safe-read transient/throttled conditions |
| HTTP Retry-After | Numeric, finite, at most 30 seconds; longer/invalid values block |

The deadline is cooperative, not a strict wall-clock cancellation. In-flight requests and bounded retries/sleeps can exceed 60 seconds before the next check; capability probes are outside that scan budget. Many multi-barcode variants require additional requests. Large catalogs or low available query cost may therefore never complete within the existing budget. Do not raise limits automatically, treat a partial scan as complete, or activate writes after a failed scan. Performance changes require separate review.

The report includes HTTP attempts by fixed query type, status counts, sanitized GraphQL extension error categories, throttle response counts, timeout counts, aggregate HTTP time, maximum request time, available cost/restore rate, elapsed time, fully examined variants, typed/untyped/secondary barcode counts and variants/second. A response containing both HTTP 429 and GraphQL THROTTLED contributes two throttle signals. Partial counts describe only fully read variants before failure. Rechecking the target does not double-count it. There are no raw response bodies, error messages, tokens, product titles, customer records or full barcode lists in output. An explicitly selected candidate contains only the identifiers needed for its separate review; share that report only through an authorized channel.

Operator checklist for interpreting the JSON:

1. Require `verified_shop`, `served_api_version=2026-10`, readable schema and appropriate observed scopes; record the report time and configured catalog-size limit without exporting variables.
2. Require `status=READ_ONLY_SCAN_COMPLETE`, `coverage_complete=true`, stable catalog membership, nonzero variant count and zero blocked requests. Review throttling/cost/duration and reconcile accessible count with expected catalog scope. Repeated successful scans can measure variability but do not establish atomicity.
3. Any API error, timeout, page/identity contradiction or budget failure means incomplete coverage. Keep writing blocked and review the safe reason code. No truncated or skipped page is acceptable.
4. For a proposed allocation additionally require a complete collision scan, no remote values, unchanged local ledger/variant, exact matching IDs and `candidate_reason=PROPOSED_ONLY_NOT_AUTHORIZED`. Otherwise no candidate is available.
5. Return only sanitized JSON and operational observations. Do not return secrets, screenshots of variables, raw SQL dumps or authenticated headers.

## Proposed single-variant pilot — not authorized

No live candidate is currently identified. After an authorized operator returns complete read-only evidence, prepare a review naming exactly **one** immutable allocation ID, local code, shop, product GID, variant GID, current version and corresponding administrator approval. Do not use a supplier/manufacturer-populated variant. Capture its full empty remote collection and before-state of SKU, price, inventory, title/options/images for comparison through a separate reviewed read-only procedure.

Before requesting write authorization, resolve independent documentation access/review, an isolated development-store mutation/read-back test, current live compatibility, visibility/collision coverage, budget adequacy and the concurrency policy. Schema introspection alone is insufficient. The source gate must remain false until a separate reviewed release decision; production flags remain disabled now. Any future gate/flag activation is outside this task and needs explicit authorization.

The owner must separately approve that one exact variant/code update and its activation procedure. A later pilot must retain existing administrator approval, locks, idempotency and immutable history; repeat full preflight immediately before its single minimal update. Acceptance is not VERIFIED: read back the entire collection and require the exact selected untyped value, then verify unrelated fields unchanged. On uncertain results do not blindly retry; reconcile through existing recovery/read-back handling. Never clear a supplier barcode or automatically remove a barcode as rollback. Stop before any second variant. This document grants no write authorization.

## Validation and remaining work

Automated results are recorded below after the final run. Tests use schema-backed mocked Shopify responses and a disposable **local** PostgreSQL database; they do not validate live store performance or mutation behavior. Mutation-path tests that temporarily override the semantic gate or enable the two write flags were deliberately deselected for this task. Existing mutation fixtures are imported only for their read-only mock responses; their mutation operations are never dispatched.

Remaining live work: authorized operator execution of the reviewed command; observed full pagination/catalog coverage and acceptable timing/cost; unambiguous existing allocation selection; assessment of access visibility and concurrent-edit risk. No production writes should be enabled from this report.

### Executed test results

- Selected read-only/CRM regression suite: **251 passed, 0 failed, 1 skipped, 9 deselected** in 82.77 seconds. Covers new diagnostic, schema-backed pagination/collisions, adapter refusal/version/scope checks, schema exporter, retail import/labels/filters, Shopify catalog, Draft Orders and WhatsApp quick messages.
- The skipped PostgreSQL-only Draft Order two-session claim regression was run separately in a disposable local schema: **1 passed, 0 failed** in 3.62 seconds. It is no longer an unexecuted case.
- Diagnostic-specific suite: **28 tests**, including actual PostgreSQL read-only transaction enforcement, rollback/no-commit behavior, full secondary pagination, failures, redaction, candidate checks and network mutation rejection. Included in the 251-test run and rerun after final transport/report hardening.
- The first mixed run selected the uninstalled `psycopg` driver and failed only that PostgreSQL setup. Rerunning with installed `psycopg2` resolved it; the final selected regression run has no failures. No production connection was used.
- Nine existing test cases that override the semantic gate or enable the two write flags were intentionally deselected to preserve this task's restrictions. They are not represented as passing in this report.
- `git diff --check` passed. The deployed adapter source and owner-supplied schema fixture hashes are unchanged. No migration was added.

For a future pilot concurrency policy, coordinate an explicit pause of other barcode writers/integrations and administrative barcode edits for the approved window; require owner review of residual race risk. This is operational coordination, not an API uniqueness lock or snapshot guarantee. Nothing in this task pauses services or changes live integrations.
