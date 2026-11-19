# Phase 1B: Shopify barcode pagination limit correction

2026-11-19 (Asia/Colombo). Based on main `c10d5d0`.

## Trigger and fix

The authorized operator reported that allocation **1**, internal barcode **P1**, SKU **1001200**, Kids Projector Torch - Pink (`gid://shopify/ProductVariant/47281619206447`), failed the individual-variant read with HTTP 200 and GraphQL `BAD_REQUEST`: `first cannot exceed 20`. The operator also reported a successful accessible-catalog scan of **2,259 variants in approximately 25 seconds without throttling**. These are owner-supplied live observations, not requests performed by Codex. They do not establish complete P1 eligibility or zero collisions.

`retail_barcode_shopify.READ` now requests `barcodes(first:20,after:$after)` and `PAGE_SIZE=20` bounds individual barcode responses. Cursor progression, explicit `hasNextPage`/`endCursor` validation, duplicate detection, full page exhaustion, scan budgets and target reread remain intact. A final short page is accepted; continuing pages are never silently truncated.

Catalog queries remain unchanged: 50 variants/catalog page with the first barcode, and 100 IDs/verification page. Because the generic connection validator defaults to the barcode page size, `_catalog_ids` now explicitly supplies `limit=100`. This prevents a valid catalog page with more than 20 IDs from being incorrectly rejected. All secondary barcode pages still participate in collision scanning.

The diagnostic pins the actual reviewed adapter source hash:

```
180dc2501e3dbb262133ffb682f5c56fa7573830afe25914fef7f7223b7d0f93
```

The diagnostic also retains the sanitized `BAD_REQUEST` error category instead of grouping it as `OTHER`. Raw Shopify error messages/bodies are still excluded. Source and diagnostic must be reviewed/deployed together; a mismatching deployed adapter remains blocked.

## Safety and fixtures

`MUTATION_SEMANTICS_VERIFIED=False` is unchanged. Neither write flag was enabled. The authentication/client, imports, Draft Orders, permissions, approvals, database schema and webhook configuration are unchanged. No live requests or mutations, production database changes, allocations, automatic deployments or merges were performed. The P1 allocation and supplier/manufacturer barcode data were not touched.

The existing schema-export JSON fixture is unchanged: this runtime maximum is an argument constraint, not a GraphQL type-shape change. The transport fixture now enforces a maximum of 20 and returns the observed HTTP 200 / `BAD_REQUEST` when exceeded. Its successful pages contain at most 20 values.

Regression cases cover oversized requests failing closed; successful complete 20-item pages; continuation offsets and a final short page; repeated/malformed cursors; secondary collisions; full catalog verification with more than 20 IDs; sanitized diagnostic failures without a candidate; and PostgreSQL-enforced read-only, rollback-only allocation reads. Tests that override the semantic source gate or enable write flags are deliberately excluded for this task.

## Operator rerun after separately authorized deployment

No deployment occurs in this task. After the fix is reviewed and deployed through the normal approved process, run inside the existing authorized Railway application container:

```sh
python -m scripts.validate_shopify_barcode_readonly --allocation-id 1
```

Use the existing connection; do not export credentials, change flags, run migrations or start a synchronization job. See the [credential-safe procedure](retail-barcode-readonly-validation.md) for full details. The allocation is read in a PostgreSQL read-only transaction and always rolled back; the diagnostic seals transport against mutations.

Require the actual report to show verified shop/version/read schema, complete coverage, stable catalog membership, `collision_check_for_candidate=true`, `collision_count=0`, an empty complete remote barcode set and an unchanged, unambiguous local P1 allocation before proposing it. No such successful P1 report is claimed here. Any permission, page, timeout, consistency or budget problem remains a blocker. A larger number of barcode continuation requests can increase duration; the 60-second cooperative scan budget is unchanged.

A successful read-only report is not write authorization and does not prevent concurrent Shopify edits. Keep the source gate false and flags disabled. Any exactly-one-variant pilot still requires separate approval and the concurrency/verification safeguards in the [pilot plan](retail-barcode-readonly-validation.md#proposed-single-variant-pilot--not-authorized).

## Automated validation

Final results are recorded below after the regression runs complete. All Shopify responses are mocked; PostgreSQL testing uses disposable local schemas, never production.

- Final selected suite: **255 passed, 0 failed, 1 skipped, 9 deselected** in 88.21 seconds. Includes barcode pagination/diagnostic, schema/adapter guards, retail imports/labels/filters, Shopify catalog, Draft Orders and WhatsApp quick messages. The actual PostgreSQL read-only allocation test passed within this run.
- The skipped PostgreSQL-only Draft Order concurrency regression was separately executed in a disposable local schema: **1 passed, 0 failed** in 3.94 seconds. Thus no selected case remains unexecuted because of the mixed SQLite run.
- Nine existing cases that override the semantic source gate or enable write flags were deliberately deselected, not counted as passing.
- `git diff --check` passed. The updated adapter hash matches the diagnostic pin. No migration is needed.
