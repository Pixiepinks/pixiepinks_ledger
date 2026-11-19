# Phase 3 deployment, compatibility evidence and recovery

## Implementation and current blockers

This branch supplies the normal inventory document, approval, dispatch-intent, stock observation, count, reorder/report and pending-transfer architecture. It does **not** claim production-ready mutation compatibility. Shopify documentation retrieval from this execution environment failed with `CONNECT tunnel failed, response 403`. No authorized Railway runtime tool/connection is available here. No live store queries, mutations, scope changes, environment changes or deployments have been performed.

The mutation adapter is a **proposed fixture-tested contract**, with an unconditional source-level production block. It uses an explicitly versioned 2026-10 endpoint through `ShopifyCatalogClient` authentication. It proposes `inventorySetQuantities` for `on_hand`, per-item/location `compareQuantity`, an idempotent directive key, and a document URI. It never uses `ignoreCompareQuantity`, absolute available resets, global barcode-write flags or automatic retries. Before release, authoritative evidence must establish:

1. The exact 2026-10 mutation/input type names, scalar types, fields and required fields; whether the quantity input is actually `InventoryQuantityInput`; the accepted `on_hand` name and reason; and the required scopes. Do not infer this from fixtures.
2. The actual supported idempotency mechanism, directive position/key argument/scalar, key retention/replay semantics and collision behavior. Introspection alone cannot certify idempotency or concurrency semantics.
3. The documented compareQuantity behavior for on-hand, interaction with committed/available/reserved states and concurrent orders/fulfillments, and multi-line partial/error behavior.
4. The actual adjustment group return shape (including whether an ID is exposed), and authoritative correlation/reconciliation using referenceDocumentUri/idempotency key. A matching current quantity is insufficient proof of a prior uncertain write.
5. The native transfer API lifecycle, permissions, inventory reservations, dispatch/receipt behavior, concurrency and recovery contract. Until verified, transfers stay pending and never dispatch as a pair of adjustments.

Expected read scopes are `read_inventory` (or `write_inventory`), `read_locations` (or `write_locations`), and product read access for variant/product metadata. Writing requires `write_inventory`. The reader executes fixed queries, checks shop/API-version identity, validates exact product/item/location/tracking state, and requires both named integer quantities. Errors fail unavailable. The runtime mutation probe checks expected shapes, installed scopes and the idempotent directive; it does not remove the independent source verification requirement. API version mismatches fail closed; global catalog API and webhook versions are untouched.

Official references to review from an authorized connection:

- https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/inventorySetQuantities
- https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/InventorySetQuantitiesInput
- https://shopify.dev/docs/api/admin-graphql/2026-10/objects/InventoryItem
- https://shopify.dev/docs/api/admin-graphql/2026-10/objects/InventoryLevel
- https://shopify.dev/docs/api/usage/idempotent-requests
- https://shopify.dev/docs/api/usage/access-scopes

Do not flip `MUTATION_CONTRACT_VERIFIED` until those exact issues are resolved in a reviewed follow-up. Update fixtures and the adapter to the **actual** supported contract where they differ, then execute the concurrency/error suites again. The current block is due to missing API evidence, not unreconciled physical stock. No P1-style special pilot subsystem is required or supplied.

## Reviewed read-only Railway diagnostic

An authorized operator can run inside the existing deployed Railway application runtime, after this code is deployed through the ordinary reviewed process:

```sh
python -m scripts.validate_inventory_readonly
```

Then, using a known CRM variant ID and actual location GID from the returned location list:

```sh
python -m scripts.validate_inventory_readonly --variant-id <CRM_VARIANT_ID> --location 'gid://shopify/Location/<ID>'
```

Use actual selected identities; do not paste shell placeholders literally or choose an arbitrary operational transaction. These commands use existing credentials in memory. Do not echo environment variables, tokens or secrets. No Railway variable changes or new app are required. The diagnostic rejects mutation calls and uses a PostgreSQL READ ONLY transaction for local identity reads. It performs no migrations, record changes or model commits. It reports adapter SHA-256, required capability booleans, actual location identity/state, the selected observation, API version and `write_authorized:false`. `READ_ONLY_COMPLETE` is read evidence only; it is not stock-write authorization or proof of mutation semantics. Capture sanitized output in the release review. The diagnostic can fail as a whole if inventory schema introspection is denied; retain that explicit blocker rather than bypass it.

Reads are targeted, not catalog-wide stock scans. Locations request 50/page with strict cursor validation, maximum 20 pages, and a cooperative 20-second budget. Document snapshots allow at most 10 lines and a 20-second quantity-read budget after the capability probe. Current-page reports allow 10 products and a 16-second cooperative budget. Each request has an 8-second HTTP timeout (3-second connect), without throttle retry/sleep; an in-flight request, authentication and capability probe can extend wall time beyond cooperative budgets. API errors, missing levels, missing scopes, untracked products and budget exhaustion never become zero. No inventory tracking/location activation is done automatically.

## Deployment steps (manual review, no automatic deployment)

1. Review the feature branch and test report. Take the normal database backup before migrations. Do not merge or deploy automatically.
2. Run the standard `alembic upgrade head` process. Additive migration: **20261119_45**, parent **20261119_44**. It creates `inventory_documents`, `inventory_events`, `inventory_count_observations`, `inventory_policies` and `inventory_policy_events`; installs PostgreSQL/SQLite append-only and document identity/attempt fences. It neither imports Shopify products nor backfills stock values nor edits production rows.
3. Leave `SHOPIFY_INVENTORY_WRITE_ENABLED=false`. `MUTATION_CONTRACT_VERIFIED=False` remains source enforced. Existing barcode variables and Shopify credentials stay unchanged. `INVENTORY_ENABLED=false` defaults off; after review, an authorized operator may enable only the inventory UI/read/document feature (`INVENTORY_ENABLED=true`, with existing Retail enabled). This document is an instruction for that operator, not a configuration change performed by Codex.
4. Grant view plus prepare/manage permissions only as needed. Only an active `ADMIN` can approve, reject, record transfer confirmations or reconcile uncertain inventory events. Existing section/session checks and CSRF protect API actions. The background worker revalidates creator and approver access.
5. Execute the read-only diagnostic. Check the live on-hand/available/location values against Shopify Admin for a known product/location, an untracked item and a real zero. Record permissions/version evidence without modifying scopes. Request any new scopes in a separately approved app-permission change only if needed; there is no automatic scope escalation.
6. Once authoritative mutation/transfer compatibility is reviewed and implemented, release the source gate in a separate reviewed change and enable the distinct inventory write flag through the normal owner-approved process. Reject/review stale pre-release approvals: approvals expire after 30 minutes and every dispatch checks fresh identities/balances. No queued job can bypass the source or setting gate.
7. Test normal **explicitly submitted and administrator-approved** real receipt/adjustment/count transactions with known supplier, variant, quantity and actual location. Do not create arbitrary “test stock.” Retain supplier invoice keys, document references, dispatch intent, Shopify references and read-backs as normal history. Test native transfers only when their separate contract/lifecycle is implemented and reviewed. Existing pending transfer records must never be automatically backfilled.
8. Verify stock reports/history, reservations versus on-hand, order/fulfillment concurrency, network loss, worker interruption and reconciliation. There is no physical full-store stock prerequisite; that exercise follows POS completion.

## Transaction and recovery invariants

- Input/reference/shop/actor/dispatch key never change. Supplier invoice keys prevent a receiving invoice from being reposted under a new request key. Rejected invoices keep their key: corrections must explicitly refer to the original history with an agreed distinct supplier reference.
- Baselines, plans, approver identity and approval hashes can be set once only. Observations and audit events cannot be updated/deleted. Before/after and read timestamps remain durable.
- A per-shop database lock serializes local transitions and worker claims. A committed DISPATCHING intent and permanent `attempts=1` fence precede network I/O. A second worker cannot send it again. DISPATCHING older than two minutes becomes UNCERTAIN, even when the write flag is off.
- UNCERTAIN/APPLIED_REVIEW blocks later inventory dispatch until explicit administrator reconciliation. No retries, compensating adjustments, forced CAS bypass or attempts-reset endpoint exist. Disabling the flag after intent may produce uncertainty even if no request was sent; this is intentionally conservative.
- A confirmed remote response is followed by complete per-line read-back. Concurrent changes or unavailable read-back produce APPLIED_REVIEW; they are not treated as failed writes. A successful read-back is an observation at that time, not a lock against subsequent Shopify changes.
- Reconcile records fresh reads and required administrator evidence. It never calls a mutation or infers prior success solely from matching balances. Its final state is RECONCILED_MANUALLY, not API-VERIFIED. Investigate Shopify inventory history and intervening order/transfer/fulfillment activity before closing review.
- The proposed on-hand CAS updates only explicit selected quantities. It must be contract-verified before release; no claim is made that read-before-write alone prevents concurrent edits. Physical count cutoff/movement controls remain necessary, including ABA/net-zero changes and available-only reservations between observations.

## Future POS boundary

Reuse `retail_service.scan_page`, shared product/version identity, imported Decimal price/currency and `inventory_service.stock` for product/availability context. Reuse inventory document/audit services for authorized manual stock business events.

Shopify order/Draft Order/fulfillment/refund lifecycle remains authoritative for its stock events. Do **not** deduct/receive inventory again when those operations already do it. This branch adds no sales/order listener and does not change existing order integration. Future POS sale orchestration must persist a unique business-event/order reference and choose one deduction authority before adding any sale operation. `RECEIPT`, `ADJUSTMENT`, `COUNT`, `OPENING` and pending `TRANSFER` are the only inventory document kinds; a scanner lookup never deducts stock. Discounts, tax, order cancellation/restocking and return flows must reuse the same authority policy, not a parallel local balance.

## Rollback

Before rollback, an authorized operator disables the distinct inventory write flag and pauses further approvals. Do not change barcode flags. Inspect DISPATCHING/UNCERTAIN/APPLIED_REVIEW records and preserve their references/evidence; a timeout or code rollback cannot undo a remote inventory change.

Revert the feature code through the normal reviewed deployment process. Retain additive tables and audit triggers; the Alembic downgrade intentionally does not drop them or erase history. Do not restore an old stock quantity blindly, run compensating mutations automatically, edit approved JSON, reset attempts or delete documents. If a real correction is needed, investigate Shopify changes and submit a new explicitly reviewed business transaction after compatibility is verified. UI-only rollback needs no Shopify rollback and no product data change.
