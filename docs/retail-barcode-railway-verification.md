# CRM → Shopify barcode verification through the existing Railway runtime

Client date: 2026-11-19 (Asia/Colombo). Branch: `feature/retail-barcode-railway-verification`. Base: current main `c0ba283` (PR #168 merged).

## Result

**The production barcode update adapter remains blocked.** No live mutation, deployment, write-flag change, scope change, production database migration or production environment change was performed. No live verification result or eligible pilot variant is invented.

The barcode adapter already reuses the existing authentication. `BarcodeClient` in `retail_barcode_shopify.py` inherits `ShopifyCatalogClient`, including its configuration, configured store URL, client-credentials token request, cached token, expiry margin and token lock. `shopify_draft_order_service.create_shopify_draft` uses that same base client. A different Shopify app or new Shopify credentials are neither necessary nor implemented.

The remaining blockers are **an available approved Railway execution channel** and **verified complete barcode semantics**, not a replacement authentication implementation. This workspace has no Railway CLI, Railway connector, Railway token or linked-project reference and no live Shopify configuration. The required official BarcodeInput documentation request still returned a proxy 403. No secrets were requested in chat, exported from Railway, copied to this repository or printed.

## What changed

- Added `scripts/export_shopify_barcode_contract.py`, a read-only evidence command that imports only existing `settings` and `ShopifyCatalogClient` modules. It can be reviewed and executed inside an existing authorized Railway container without adding any app or credentials.
- The command requests barcode-specific API **2026-10**, leaves the global API version unchanged, rejects an unverified served version, and matches the returned shop identity to the client's existing configured store.
- It observes current installed `read_products` / `write_products` handles and exports technical schema definitions for the variant barcode field, bulk barcode inputs, BarcodeInput, bulk mutation arguments and related return/input/enum/union types. Input defaults are observed rather than invented.
- Related type names are discovered from returned introspection metadata. Type traversal detects cycles, validates returned names, has a 12-type budget and reports incomplete evidence explicitly. Fixed read-only query documents are whitelisted. There is no caller-supplied GraphQL or mutation transport.
- Output is limited to shop/version/scope evidence and schema metadata. It does not retrieve product/customer records or credential values, query SQL, select variants, dispatch jobs or authorize writes. Unexpected errors are sanitized. The named Barcode Lab connection is refused.
- The command always exits **2**, with `status: BLOCKED`, `contract_verified: false`, complete-barcode/collision verification false and no pilot variant. Exported metadata is evidence for review, not proof that the mutation is safe.
- Added mocked tests for existing-client token reuse, fixed documents, isolated API version, missing connection, wrong shop, fallback, throttling, errors, related type cycles/budgets, union/default evidence and retained write refusal.

The production barcode adapter, approval worker, importer, Draft Order client, authentication, SKU/price/inventory handling, label generation and CRM implementation were **not changed**. The existing write method still refuses every write. Local allocations and all audit records remain intact.

## Approved, credential-safe Railway procedure

The authorized operator needs a remote execution method **inside the existing application container**. The operator supplies only the non-secret project/service reference and approved access method; credentials remain in the existing Railway environment.

If the reviewed command is already available in that runtime, run from the application's working directory:

```
python -m scripts.export_shopify_barcode_contract
```

If it is not deployed, do **not** deploy automatically merely to run it. The file is self-contained relative to the existing `settings` and `shopify_catalog_service` modules and can be supplied as reviewed source to an approved remote Python stdin session in that working directory. That executes the read-only evidence collector in memory without writing application files or changing variables. Establishing that approved execution channel is required; this task did not have one and did not run the command on Railway.

Do not export the production environment into Codex or use an execution mode that downloads the Railway variables locally. Do not paste credentials, tokens, environment dumps or authenticated headers into chat. Return only the command's whitelisted JSON evidence through the approved channel. Exit 2 is expected and must not be interpreted as permission to enable writes.

This is a preparation procedure, not evidence that remote execution occurred. The actual local run reported `RAILWAY_RUNTIME_CONNECTION_UNAVAILABLE` with the missing setting **names** only. The checked configuration names are `SHOPIFY_SHOP`, `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET`; no values were printed.

## Verification matrix

| Required result | Current result |
| --- | --- |
| Authentication/token/store code reused | Confirmed by source inspection and mock transport tests |
| Actual configured live Shopify identity | Not verified: approved Railway runtime execution unavailable |
| Actual served barcode API version | Not observed; command explicitly requests 2026-10 |
| Actual installed scope handles | Not observed |
| Actual BarcodeInput / barcode collection definitions | Not obtained; synthetic fixtures are not official evidence |
| Complete existing barcode state for a real variant | Not read or verified |
| Full-catalog collision coverage including secondary barcodes | Not verified |
| Safe non-destructive custom/untyped mutation semantics | Not verified |
| Real variant IDs matched to the CRM allocation ledger | Not verified |
| Approved one-variant pilot | None selected or authorized |

## What remains before the adapter can be completed

1. Execute the read-only evidence command through an approved existing Railway session. Do not change its environment or install a new Shopify app. Verify the returned live shop, served API version and installed scopes.
2. Make the official contract accessible and review the actual type evidence. Introspection field names alone do not prove overwrite/replacement semantics or custom/untyped behavior.
3. Implement and validate complete barcode collection retrieval with correct documented pagination/response checks. An empty legacy barcode is insufficient. Do not invent BarcodeInput enum values or send deprecated and new barcode fields together.
4. Verify full remote catalog collision coverage, including secondary barcode slots. Local uniqueness or unproven legacy barcode search coverage is insufficient; partial or inaccessible scans must block the operation.
5. Only after the contract is verified, implement the minimal approved update for the exact variant, preserving every other product field. Keep the existing Phase 1B snapshot/permission/flag/scope/lease/idempotency checks and read-back-only VERIFIED rule. No automatic overwrite or blind retry.
6. Test the verified contract using mocked mutation responses, including uncertainty/partial errors, then prepare an exact real candidate under the [existing blocked pilot plan](retail-live-barcode-pilot-plan.md). Obtain separate owner authorization naming that variant and code before any actual update. Preflight/write is not atomic remote compare-and-swap.

The current exporter intentionally does not read sample variant records or attempt an incomplete collision check before the schema is understood. No such unperformed inspection is recorded as success.

## Tests

Final selected regression run: **411 passed, 0 failed, 3 skipped** in **219.63 seconds**. The three PostgreSQL-only AI/Draft Order cases skipped by the mixed SQLite run were executed separately: **3 passed, 0 failed, 0 skipped** in **4.03 seconds**. None of those three cases remains unexecuted.

The finalized evidence exporter module passed **12 tests**, including the added check that unrelated required bulk input fields remain visible for schema review without traversing or setting them. An earlier focused evidence/readiness/workflow run passed **64 tests**. These overlapping runs are not summed into an inflated total. The final selected mixed run includes seven retail PostgreSQL migration/concurrency cases. No final automated failures remain.

JUnit artifacts: `/tmp/railway-barcode-regressions.xml`, `/tmp/railway-barcode-extra-pg.xml`, `/tmp/railway-barcode-evidence-final.xml`, `/tmp/railway-barcode-unit.xml`. Python compilation and `git diff --check` passed. Local command output was retained in `/tmp/railway-barcode-local-evidence.json`; it reports missing runtime configuration and is not a live Shopify verification.

The final selected suite includes contract evidence, live readiness, barcode adapter/workflow, PostgreSQL migration/concurrency/recovery, Phase 1 imports and labels, shared-barcode selection, staff access/notifications, Shopify catalog and Draft Orders/order lifecycle, WhatsApp Quick Messages/Inbox and Global AI. Every external mutation or message is mocked. Databases are task-owned SQLite files and disposable localhost PostgreSQL schemas. This is a selected regression set, not the full repository suite.

Representative invocation:

```
python -m pytest \
  tests/test_shopify_barcode_contract_evidence.py tests/test_live_barcode_readiness.py \
  tests/test_retail_barcode_adapter.py tests/test_retail_barcode_sync.py \
  tests/test_retail_barcode_sync_postgres.py tests/test_retail_postgres.py \
  tests/test_retail_phase1.py tests/test_retail_product_filters.py \
  tests/test_compact_sku_price_label.py tests/test_staff_access.py \
  tests/test_shopify_catalog.py tests/test_shopify_order_lifecycle.py \
  tests/test_shopify_draft_order_sync.py tests/test_staff_notifications.py \
  tests/test_whatsapp_quick_messages.py tests/test_crm_whatsapp_inbox.py \
  tests/test_global_ai_control.py -q --disable-warnings --tb=short
```

Schema fixtures are explicitly synthetic. Passing them does not verify actual Shopify collection pagination or mutation semantics. Live Railway/store compatibility checks remain not run.

## Migration, deployment and pilot

No new migration is needed. Existing Phase 1B revision `20261118_43` remains a prerequisite for the underlying queue. No production migrations, deployments, scopes, variables or flags were changed. Both write flags remain disabled by default. The initial batch limit remains five; an eventual pilot uses exactly one separately approved variant.

No real candidate is recommended without access to its current remote state and committed CRM allocation. The existing blocked pilot plan requires the actual shop/product/variant IDs, SKU, immutable local barcode, complete remote barcode collection, full collision evidence and exact expected change. None of those values may be replaced by fixture IDs or examples.

## Changed files and delivery

- `scripts/export_shopify_barcode_contract.py`
- `tests/test_shopify_barcode_contract_evidence.py`
- `docs/retail-barcode-railway-verification.md`

Final delivery provides the feature commit and PR/review link. The write adapter remains blocked until the specific live-access and contract evidence requirements are satisfied.
