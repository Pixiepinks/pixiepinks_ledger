# PixiePinks Retail Management — Phase 1B Implementation Contract
Status: SPECIFICATION ONLY; not implemented or deployed.
Base: current main as of 2026-11-18. Dedicated branch: `feature/retail-phase1b-barcode-sync`.

## Objective
Extend the existing FastAPI/SQLAlchemy/Alembic/Jinja/vanilla JS Retail Management Phase 1 with *controlled* synchronization of previously approved, immutable local barcode assignments into **currently empty** Shopify variant barcode fields. The CRM read-only importer and physical label printing must keep working. No inventory, SKU, price, order, receipt, checkout or location mutation is permitted.

## Existing code to inspect before changes
- `retail_models.py`, `retail_service.py`, `retail_sync.py`, `retail_routes.py`, `retail_labels.py`, `retail_worker.py`, `static/retail.js`, `templates/retail.html`, `staff_access.py`, `settings.py`.
- `shopify_catalog_service.py` for existing server-only credential/token/retry patterns.
- Existing migrations (last known retail migration `20261117_42`), latest Alembic head, tests, README, deploy instructions.
- Existing shared supplier-barcode variant picker and documented duplicate/share exceptions (PR #166). Preserve explicit disambiguation and do not auto-choose a variant on a shared supplier barcode.
- Existing compact 30x15 label layout and filter behavior (PRs #164 and #165).

## Rules / safety invariants
1. Keep `SHOPIFY_PRODUCT_WRITE_ENABLED=false` by default. Add separate `RETAIL_BARCODE_SYNC_ENABLED=false` and a dedicated action permission `retail.barcodes.shopify_sync` (admin only by default). Require BOTH write flags, permission, confirmed `write_products` scope, explicit reviewed batch, and exact confirmation to perform any remote mutation. Merely enabling either flag must not launch work.
2. No new product creation or destructive update. Do not change SKU, titles, descriptions, prices, compare-at prices, inventory, collections, shipping, images, metafields, barcodes on any other variant, or existing nonempty barcode values.
3. Only sync a committed local barcode for the exact configured-shop + Shopify variant ID when the remote barcode slot(s) are confirmed truly empty immediately before write. Imported local snapshots are NOT sufficient. Use the current supported Admin GraphQL API safely.
4. Shopify introduced multiple barcodes in API 2026-10. Inspect the actual production supported version/schema and all available barcode slots when possible. Do NOT rely only on legacy `barcode` to claim a variant is empty if secondary barcodes might exist. If reliable full barcode-state inspection is unavailable, classify that item as UNSUPPORTED/NEEDS_REVIEW and fail closed. Do not silently upgrade the entire CRM Shopify API version. An internal code such as `P2` is a **custom/untyped** value, never label it UPC/EAN/GS1.
5. Block inactive/removed/stale/conflicting variants, duplicate effective scan identities, undocumented sharing, changed assignments, unknown owner/permissions, and ambiguous barcode state. Explicitly distinguish duplicate SKUs from duplicate barcodes; don't auto-fix duplicate SKUs or revoke legitimate supplier sharing in this phase.
6. No blanket "sync all" without review. Require: select/filter -> preview all candidates with Shopify ID, SKU, local/remote code, reason/status -> approval -> queue -> bounded execution -> remote read-back verification. Selections and preview tokens are short lived, immutable, scoped to staff/shop and database/version/snapshot. Recheck everything before every remote mutation. A batch is limited and can be canceled before dispatch; after write it uses verified reconciliation rather than blind rollback.
7. Persist durable jobs/items in additive migration with immutable audit events, unique idempotency keys, attempt counts, per-item statuses (ELIGIBLE, BLOCKED, QUEUED, CHECKING, SENT, VERIFIED, CONFLICT, FAILED, UNKNOWN), before/after snapshots and sanitized API errors. Don't mark VERIFIED until fresh Shopify read-back confirms exact values. If response is uncertain, re-read before retry; never blindly resend.
8. Read-only catalog imports must preserve committed local identifiers and reconcile confirmed Shopify barcodes without falsely declaring any write. Display imported/locally assigned/verified on Shopify states separately and show last check time. Protect queued/in-flight jobs against concurrent imports and allocations with appropriate locking/version or compare-and-swap rules.
9. Implement server-side role/CSRF/tenant checks, rate limits, queue recovery, retries with backoff, secrets redaction, structured logs, CSV safe export, pagination and deterministic status reports. No cross-tenant or shopper-visible credentials.
10. Use a tiny first live pilot (1-5 previously reviewed variants) only AFTER deployment approvals; no development/test live Shopify writes, no automatic production deployment, no automatic environment/scope changes.

## Shopify API approach
- Confirm `write_products` scope in the installed app; do not request additional scopes or reconfigure live installation automatically.
- For current v2026-07 API the documented `productVariantsBulkUpdate(productId:, variants:)` mutation requires `write_products`. Include only `id` and `barcode` fields in a minimal variant input, and set `allowPartialUpdates:false` where supported. Group by product only after fresh eligibility checks; safest initial pilot is one variant per mutation.
- For v2026-10+ multiple barcode support, verify available connection/input and non-destructive behavior with a fixture/schema test. Never send a `barcodes` replacement list until full current remote list is securely known and the expected semantics have been confirmed. Avoid accidental replacement of secondary codes. If this requirement cannot be met using adopted API/schema, skip the variant and document the blocker.
- Remote preflight read reduces but cannot eliminate third-party races; make this residual risk explicit, verify after write, and never claim atomic remote compare-and-swap if none exists.

## UI
- Add "Shopify Barcode Sync" view to Retail Management or a dedicated page under Barcode Management.
- Cards: approved local barcodes, eligible empty Shopify barcodes, blocked conflicts, pending, verified, failed/unknown, last remote verification.
- Table: product/variant, Shopify ID, existing Shopify code(s), local code, SKU, conflict reason, snapshot time, selection, preview, submit, job history and export.
- The sync button is absent/disabled with a clear explanation until all flags, permission and scope requirements pass. Staff cannot bypass eligibility checks by HTTP API calls.
- Confirm message explicitly states that approved local barcodes will be written into empty Shopify variant barcode fields only; no overwrite.
- Keep the current barcode label generation/print and shared supplier-code picker intact.

## Tests and acceptance
- Add unit/integration/PostgreSQL tests: empty remote, changed-to-nonempty, multiple/secondary barcode, reused code, duplicate local code, duplicate SKU, documented supplier sharing, stale preview, stale import, deleted/inactive, wrong variant/shop, permission and CSRF, scope and flags, two workers, concurrent allocation, network uncertainty, API rate limit, partial errors, crash/recovery, repeat submissions, verified read-back, export escaping, and regression for Phase 1, CRM Inbox/AI, staff notifications, existing Shopify order operations and label printing.
- Mock ALL external writes in tests. Run applicable project suites and report exactly which passed/skipped; do not claim full suite unless run.
- Provide schema/migration documentation, flags and scope approval walkthrough, staging plan, pilot instructions, rollback and recovery guide; no destructive downgrade.
- No deployment or production migration performed without explicit user approval.

## Implementation and reporting
Implement on this branch following the above with code, additive Alembic migration, frontend, tests and docs. Keep changes minimal; preserve architecture. Stop safely if Shopify schema cannot support a non-destructive update. Provide changed files, actual test outputs, commit SHA, PR link if possible, and explicit remaining production approvals.

Official references:
- https://shopify.dev/docs/api/admin-graphql/2026-07/mutations/productVariantsBulkUpdate
- https://shopify.dev/changelog/posts/product-variant-barcode-is-being-replaced-by-barcodes
- https://shopify.dev/docs/apps/build/authentication-authorization/manage-access-scopes
