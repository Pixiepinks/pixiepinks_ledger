# Single-variant live barcode pilot — NOT AUTHORIZED / NOT EXECUTABLE

Client date: 2026-11-19 (Asia/Colombo).

Read-only inspection of the existing authorized PixiePinks live connection is allowed. No live mutation is authorized by this plan. Do not use the Barcode Lab connection, alter Railway settings/scopes, enable flags or deploy automatically.

## Latest schema status

The owner-supplied 2026-10 definitions are implemented and mock-tested; see [the schema-backed adapter report](retail-barcode-schema-implementation.md). Live mutation replacement/custom-value semantics are not established by introspection. The source-level `MUTATION_SEMANTICS_VERIFIED=False` gate stays closed. A live read-only integration and an exact real candidate are still required. No test-only gate override authorizes a real pilot.

## Candidate: none identified

The execution environment has no existing authorized live Shopify connection or production CRM catalog connection. No real variant can be nominated. Test fixture variants and example `P1`/`P2` codes are not pilot candidates.

| Required evidence | Actual verified value |
| --- | --- |
| Live shop identity, returned by Shopify | Not available |
| Served barcode API version | Not observed; diagnostic request version would be 2026-10 |
| Installed product scopes | Not observed |
| Existing CRM product / variant record | Not identified |
| Exact Shopify Product GID | Not identified |
| Exact Shopify ProductVariant GID | Not identified |
| SKU (preserve unchanged) | Not observed |
| Committed local barcode and allocation-ledger ID | Not observed |
| Complete current Shopify barcode collection | **Not observed; do not assume empty** |
| Complete catalog collision coverage | Not verified |
| Expected mutation | Not approved; no payload prepared |

This is a blocked pilot plan, not a reviewed change. The exact requested single variant and change cannot be supplied until the live read-only evidence exists.

## Preparation after the missing connection and contract are available

1. Use the existing authorized application configuration; verify the returned shop domain matches it. Run `python -m scripts.verify_live_barcode_connection` for read-only metadata. It exits 2 while blocked and cannot authorize a write. Supply credential bindings securely outside chat; do not invent or substitute store credentials.
2. Verify the official 2026-10 collection/input/mutation semantics and actual installed schema. A field name or a null legacy barcode is insufficient. No schema-discovery mutation is allowed.
3. Read the current CRM catalog/immutable assignment ledger. Select exactly one existing active, fresh, unambiguous variant with a committed local code and no conflicting identifiers or undocumented duplicate SKU/barcode. Record its exact shop, product GID, variant GID, SKU, local code and ledger/version snapshot in the table above.
4. Retrieve every barcode slot/page for that exact variant and prove the complete collection is empty. Complete the cross-catalog collision scan, including secondary codes. Record scan time/coverage and fail on partial pagination, stale/ambiguous state, access errors, throttling or unknown semantics. Preserve documented supplier sharing.
5. Capture non-barcode product/variant fields, especially SKU, prices, inventory, options, images, titles, descriptions and collections, for comparison after the eventual pilot. Complete the exact expected change: **empty complete barcode collection → one unchanged approved local custom/untyped code**. Do not choose a UPC/EAN/GS1 type for internal codes or prepare both deprecated `barcode` and new `barcodes` inputs.
6. Obtain the owner's separate authorization naming that exact variant and exact code. Then obtain any separate deployment/flag authorization needed, using the existing Phase 1B reviewed preview/confirmation queue with one item. The initial limit remains at most five; this pilot uses one. Nothing in this plan grants these approvals.
7. During a controlled catalog-editing window, the approved worker must recheck complete remote state immediately before the minimal mutation, dispatch once, and read back. Verify the exact code and all preserved fields; then perform an ordinary read-only import and scan lookup. Record VERIFIED only after full read-back.
8. Stop on BLOCKED, CONFLICT, FAILED or UNKNOWN. Reconcile uncertain outcomes by reads; never blindly resend, remove a remote code, or overwrite a supplier code as rollback. Retain all audit evidence and reservations.

Remote preflight/write is not an atomic compare-and-swap. Other Shopify editors can race with inspection; local locks cannot prevent those changes. No pilot should proceed while compatibility or complete catalog coverage remains unknown.
