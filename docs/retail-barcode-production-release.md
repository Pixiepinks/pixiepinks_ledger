# Normal CRM → Shopify barcode synchronization: production release

2026-11-19 (Asia/Colombo). Base main `5f1ef8a`. Branch `feature/retail-barcode-production-release`.

## Result and evidence boundary

This release prepares **ordinary, permanent batch synchronization in the existing CRM interface**, rather than another P1 exception. No live Shopify request/mutation, production database migration, Railway variable change, automatic merge or deployment was performed during development. Production activation remains an operator action after release review.

The owner reports the completed P1 pilot as `PILOT_VERIFIED`, one mutation attempt, matching full read-back and protected product comparisons, and visual verification in Shopify Admin. The previously verified accessible catalog had 2,259 variants and took approximately 26 seconds to scan. [Sanitized evidence provenance](evidence/barcode-production-p1-owner-report.json) records this as **owner-provided production evidence**, not an independently executed Codex request. The supplied actual 2026-10 schema fixture and existing schema-backed mutation tests remain in use.

Official contract references:

- [ProductVariantsBulkInput 2026-10](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/ProductVariantsBulkInput)
- [BarcodeInput 2026-10](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/BarcodeInput)
- [Shopify barcode replacement changelog](https://shopify.dev/changelog/posts/product-variant-barcode-is-being-replaced-by-barcodes)

The owner-confirmed official semantics and actual P1 result establish the formerly missing release decision: `barcodes` replaces the complete set, and omitting `type` stores the internal value without assigning UPC/EAN/etc. `MUTATION_SEMANTICS_VERIFIED=True` now expresses verified adapter support. This is **not** write activation: both runtime write flags still default to `false`, live schema/version/scope checks remain mandatory, and every normal write requires a new explicitly approved release-tagged batch.

P1 is permanently retired as a write target. Pilot preparation/execution return HTTP 410; its private mutation method also refuses. Normal previews, approvals/dispatch checks and the adapter reject code `P1` or variant `gid://shopify/ProductVariant/47281619206447` in `eb2b70`. No special permit can rearm the pilot. Existing pilot records, timestamps, protected evidence and events are retained; historical status and read-only reconciliation remain available.

## How the normal workflow operates

1. Staff assign committed local barcodes through Barcode Management, then select at most five products in Product Master.
2. An active administrator makes a fresh selection and opens **Shopify Barcode Sync**. Selection tokens carry this release identity; tokens from before the release cannot start a new synchronization preview. Existing printing uses of selection tokens remain compatible.
3. **Preview** immediately queues a durable read-only background check. It does not call Shopify in the HTTP request. The existing retail worker checks one row at a time; the interface polls progress and shows checked/total counts. No approval is available for a partial preview. Canceling a pending check invalidates its claim without writing Shopify.
4. For every eligible row, the worker captures immutable local identity/version/ledger data, complete remote barcode/collision evidence and protected product attributes. Once all rows are checked, the ten-minute review period begins and the approval digest is finalized. Expired, blocked or changed rows require new selection/review, never silent repair.
5. The **same active ADMIN** who created the preview reviews product/variant IDs, SKU, local code, protected before-state, eligibility and exclusions, then types the existing exact batch confirmation. Existing dedicated retail permissions and CSRF remain required. Delegated non-admin staff may inspect/select where already permitted, but cannot approve production writes.
6. Only ELIGIBLE rows are reserved/queued. The existing worker rechecks the approver, local assignment/version, release identity, reservations, scope/API contract, active target and full live catalog immediately before mutation. Approval expires after 30 minutes to accommodate five sequential items; lease and approval expiry are checked again at the final HTTP boundary.
7. Each item has one durable dispatch intent before its sole mutation call. The mutation submits only product ID, selected variant ID and `barcodes: [{value: committed_internal_code}]`, with omitted type and `allowPartialUpdates:false`. No SKU, price, stock, title, option, image or other unrelated field is submitted.
8. Acceptance/errors/timeouts require full remote read-back. Exactly the selected code with **null type**, correct identity, complete collision evidence and matching protected fields are required for VERIFIED. Staff see VERIFIED, BLOCKED, CONFLICT, FAILED or UNKNOWN and retain the existing history/CSV interface. An uncertain dispatched item can only be reconciled by reads; the durable sent marker is never cleared or automatically resent.

Approvals remain administrator-only. Automatic approval review rejected a proposed compatibility change allowing delegated staff to approve writes, because it weakened that write-control boundary. That change was not applied; administrator checks remain in both approval and dispatch, with a regression test denying delegated staff approval.

## Safety retained and completed

- Committed, immutable local ledger assignment and exact shop/product/variant/version/SKU/code snapshot, current permission/owner, no stale/inactive/removed imports, no duplicate effective identifier or undocumented SKU sharing.
- Full unfiltered accessible-catalog enumeration, every secondary barcode page, ordered membership re-enumeration and fresh target comparison. Barcode pages remain **20**, catalog variant pages **50**, verification ID pages **100**. No search-only/local-only collision substitute or silently truncated connections.
- Default scan budgets remain: 60-second cooperative catalog deadline, 10,000 barcode values/variant, 200,000 logical reads, and the lower configured/catalog variant limit. Read retries/throttling remain bounded. No budget was removed for speed.
- Complete empty target collection and compatible legacy field required. Any manufacturer/supplier value, even a secondary value, blocks the update. The final target read is followed by a five-second freshness bound, checked again after authentication refresh.
- The general protected reader reuses the actual pilot's field set: SKU, price/compare-at price, tax/position/policy fields, aggregate inventory, selected options, variant image, inventory-item identity/SKU/tracking/shipping, all accessible inventory levels/location IDs and eight quantity categories, product title/handle/status/description/vendor/type/tags/options/featured image and images. Both connections exhaust 20-item pages within a 100-page/60-second bound. Empty SKU and untracked/null inventory retain their original values. Missing/denied fields block; no scopes are added.
- Protected values are compared to the approved preview before dispatch, inside the adapter after its fresh collision scan, and after read-back. Normal after-state and immutable read-back events retain the comparison evidence. Automatic `updatedAt` timestamps and every possible metafield/hidden app resource are not claimed as covered; the minimal payload cannot submit changes to those fields.
- The same `ShopifyCatalogClient` authentication/token management is reused. Global API/webhook version remains **2026-07**; barcode operations remain explicitly **2026-10**. No import, Draft Order or webhook implementation is changed.

### Non-atomic replacement risk

Shopify offers replacement semantics here, not an empty-field compare-and-swap or cross-catalog uniqueness lock. Complete scans and a last empty target read **cannot prevent an external edit between read and mutation**, nor prove visibility of inaccessible variants. A late supplier barcode could be overwritten by the replacement operation despite having been empty at the last check; a late P-code insertion elsewhere could introduce a collision.

Before activation, the owner must approve a writer-coordination policy: make the CRM the authoritative writer for these empty-field assignments, and prohibit competing manual/CSV/app barcode updates while CRM batches are active. Coordinate scheduled integrations and Shopify editors; do not stop shared WhatsApp or Draft Order services. Check catalog count/scope against the expected store catalog during release review. The remaining race is an operational risk requiring signoff, not something a successful scan eliminates. Ordinary sales/inventory changes may also make protected comparison fail; never reset inventory or prices to force verification.

## Historical job/item/reservation activation fence

Migration `20261119_44` adds nullable job fields `release_version`, `preview_selection`, `preview_capability`, `preview_claim`, `preview_lease`, an index, and a database trigger freezing release identity, selection, actor ID and token. There is **no backfill**: historical jobs remain NULL forever. The trigger forbids retagging old jobs into this release. Item review snapshots/before-state and events keep their existing immutable triggers.

New normal jobs are explicitly tagged `20261119_standard_v1`. Old PREVIEW, QUEUED, RUNNING or expired approvals cannot dispatch a mutation when flags become true. Old attempted work may be read back, but never sent again. Pending historical issues/reservations also block fresh approval and normal write preflight until reviewed. Existing successful P1 records are not converted into normal jobs.

The normal interface shows historical issues and legacy reservation counts. The administrator's **Retire legacy approvals — no Shopify writes** action requires:

```text
RETIRE LEGACY BARCODE JOBS WITHOUT WRITING SHOPIFY
```

This action records the old status in an append-only event, blocks unsent old work and releases its matching reservations. Uncertain attempted work retains evidence and requires read-only reconciliation. Verified or already read-only-retired reservations can be released with an explicit audit event without changing a completed pilot's status. Exact legacy reservation records can be inspected in the authenticated browser at `/crm/retail/api/barcode-sync/reservations`; this is a paginated read-only view (100 records/page, `?offset=100`, etc.), not an update endpoint.

For historical SENT/UNKNOWN/CONFLICT/CHECKING records, use **Reconcile read-only batch** in the normal interface. The existing worker can do this with write flags off. A complete current read-back records matching untyped codes as verified barcode observations; a conclusively inspected empty/nonmatching target is retired BLOCKED with `LEGACY_READBACK_RETIRED_NO_RESEND`. A legacy job with no protected baseline explicitly records `LEGACY_BASELINE_UNAVAILABLE`, not a claim that its earlier operation preserved every attribute or caused the currently observed code. API/identity/coverage failures remain unresolved blockers. No historical reconciliation performs a mutation, changes its release identity or creates new write authority.

Current-release preflight conflicts that never reached dispatch are not rearmed by reconciliation: make a new selection/preview instead. Current dispatched UNKNOWN/CONFLICT outcomes retain the no-resend recovery/reservation behavior. Do not delete attempt markers, reset counters, force statuses or bulk-tag historical jobs to make activation pass.

## Performance and worker behavior

At the owner's reported 26 seconds/2,259 variants, five sequential previews need roughly **130 seconds plus protected reads**, making a synchronous request unsuitable. Preview creation, status polling and approval no longer perform full scans in the web request. Capability metadata is cached from background checks; dispatch revalidates it live. Progress survives restarts and expired read-only claims can resume without duplicate rows. The worker gives preview, dispatch, label and import work a turn rather than allowing barcode work to permanently starve the other retail queues.

Normal dispatch deliberately retains independent fresh scans in the worker/adapter and full read-back. A row can take roughly four catalog scans (about 104 seconds at the reported scan time, plus other reads); five rows can take several minutes. These are estimates from owner-reported input, not a live benchmark of this release. A 30-minute approval window and per-item five-minute lease are bounded, not permission to use stale remote evidence. Lease/approval expiry or scan budget failure means no mutation/UNKNOWN reconciliation as appropriate, never skipped verification. Concurrent imports/allocations invalidate changed local snapshots instead of silently adopting them.

Routine use requires no Railway console or Chrome Developer Tools. Use Product Master, Barcode Management and Shopify Barcode Sync, with the administrator approving their own completed preview. Polling preserves typed confirmation text and pauses while the confirmation field is focused.

## Production readiness checklist — operator must complete after code review

- [ ] Review/approve this code, source verification decision, exact minimal mutation, administrator checks, writer-coordination risk and test report.
- [ ] Confirm the real store is `eb2b70.myshopify.com`, existing authorized app/credentials are unchanged, and required product/protected-inventory reads work. No new scope or credential is authorized here.
- [ ] Retain the successful P1 pilot/audit and verify it still shows PILOT_VERIFIED / one attempt. Do not replay it or include it in a normal batch.
- [ ] Back up the application database through the normal approved process; apply additive migration **20261119_44** and confirm the Alembic head.
- [ ] Deploy the reviewed code to all API/retail-worker instances while both write flags remain **false**; do not run a mixed old/new barcode release during activation. Existing runtime must already have RETAIL_ENABLED configured as intended.
- [ ] Ensure `RETAIL_BARCODE_SYNC_BATCH_LIMIT=5` (new validation permits 1–5; an older larger configured value must be corrected through the approved release process before startup).
- [ ] In the normal interface, inspect historical jobs/items/reservations. Retire old approvals, complete read-only reconciliation and require **Historical issues = 0** / **Legacy reservations = 0**. Do not synthesize success if a blocker remains.
- [ ] Clear old Product Master selection, choose one eligible committed non-P1 item and verify a fresh background preview. Check complete collision evidence/empty barcode state/protected before-values; leave flags off during this preactivation check.
- [ ] Confirm the retail worker is running and background progress advances. Validate no unrelated import, Draft Order, WhatsApp or label regressions in the deployed environment.
- [ ] Approve the exact activation settings and a first **normal one-item CRM batch** smoke test. Its selection/review/approval must be fresh; no old preview is promoted.

A code-complete/tested release is not a statement that this checklist has been performed live. Codex has not inspected the current production queues/reservations or enabled the service.

## Exact Railway activation — future approved operator action only

Do not execute this section under the development request. After review, approved deployment, migration and the zero-blocker checks above, the authorized operator uses the existing Railway project's application/retail-worker service **Variables** page to set:

```text
SHOPIFY_PRODUCT_WRITE_ENABLED=true
RETAIL_BARCODE_SYNC_ENABLED=true
RETAIL_BARCODE_SYNC_BATCH_LIMIT=5
```

Apply through the approved Railway release process/restart so every relevant process loads the reviewed code and settings. Both write flags must be true; setting only one does not activate dispatch. Do not change `SHOPIFY_API_VERSION=2026-07`, webhook version, barcode API constant `2026-10`, credentials or scopes. Do not export environment variables or paste tokens into chat. Defaults in source remain false for new/unconfigured environments.

Then, as the active administrator in the normal CRM:

1. Make a **new** Product Master selection of exactly one eligible committed barcode other than P1; open Shopify Barcode Sync and request Preview.
2. Wait for checked/total 1/1 and a completed PREVIEW. Review SKU/product/variant/code, ELIGIBLE classification, empty remote collection, zero collisions and protected before-values. Any BLOCKED result means choose/review appropriately, not override the reason.
3. Type the exact confirmation shown by that new batch (for example, `WRITE 1 BARCODES TO EMPTY FIELDS IN eb2b70 BATCH <new batch id>`) and approve. Do not approve a historical batch or directly call the mutation adapter.
4. Wait for the existing worker to finish full preflight/dispatch/read-back. Require VERIFIED, matching untyped code and `protected_unchanged=true`; inspect before/after/event history and Shopify Admin's barcode/SKU/price/inventory observation. There is one DISPATCH_INTENT and at most one mutation POST per item. The UI's attempts counter includes read-check claims/retries and is not itself a count of mutation HTTP requests.
5. If verification succeeds and the owner accepts the operational result, **retain both flags true for everyday operation**, with the limit five. There is no per-product pilot permit or console procedure. Future batches use the same staff selection → background preview → administrator approval → worker flow.
6. If FAILED, CONFLICT, UNKNOWN or an infrastructure error occurs, stop additional approvals, use read-only reconciliation where offered, and follow disable/review below. Do not resend a mutation from the console or repeatedly approve an uncertain item.

No first normal smoke test was executed by Codex. The P1 owner result does not count as this normal-workflow smoke test.

## Disable, recovery and rollback

Through the approved Railway process set **both** write flags back to `false`, retaining the batch limit and existing connection. Restart/drain relevant processes so cached true values are replaced; changing a variable does not recall an already issued HTTP request. Treat any durable SENT/UNKNOWN as uncertain until read back. No new unsent writes should dispatch with flags false; read-only reconciliation remains possible.

Cancel not-yet-dispatched current batches in the interface if the intention is to abort them. Current-release unexpired approvals are valid pause/resume work and can resume when flags return true; review/cancel them before reenabling rather than assuming disabling erases approval. Historical NULL-generation approvals remain permanently fenced. After a dispatch intent, reconcile by reads only; no automatic blind resend or deletion is provided.

Rollback application code through normal review while keeping migration 44's additive columns, release fences, assignments, reservations, snapshots and events. Its downgrade is intentionally nondestructive. Do not restore the audit database to an earlier approval state, remove the release trigger, retag old jobs, clear sent markers or delete a Shopify barcode as compensation. Any corrective Shopify write requires a separately reviewed decision. Preserve manufacturer/supplier identifiers, SKUs, prices, inventory and the completed P1 proof.

## Implementation and validation report

Primary changes: verified standard contract/adapter; shared protected reader; nullable immutable release-generation fence/migration; durable asynchronous preview/check leases/progress; old-work retirement and read-only recovery; administrator-only normal approval; permanent P1 retirement; existing UI progress/polling/confirmation preservation and reservation inventory; default-off settings with batch maximum five; read-only diagnostic pin updated without requiring the obsolete source block. No generic unrestricted product-update endpoint or changes to Shopify imports, Draft Orders, webhook behavior or authentication.

Automated tests use mocked Shopify mutations and disposable local PostgreSQL/SQLite. Final totals, commit and publication status are reported with the release. Node syntax and Python compilation are also checked. Outstanding live work is the checklist, writer coordination/race acceptance, reviewed deployment/migration, zero-blocker queue inspection, operator flag activation and the normal one-item smoke test. No live connection is configured for Codex to perform these operations, and the request does not authorize automatic activation.

### Executed verification

- Broad release/CRM regression run in a **fresh** disposable SQLite database plus isolated local PostgreSQL schemas: **431 passed, 0 failed, 1 skipped** in **192.95 seconds**. Covers normal one-/five-item workflows, legacy activation fences and read-only recovery, immutable migration, concurrent workers and revocation, manufacturer/secondary barcodes and pagination, label/import consistency, Shopify catalog/Draft Orders/collections/order lifecycle/payment synchronization, WhatsApp quick messages and customer automation.
- The skipped PostgreSQL-only Draft Order two-session claim case was executed separately: **1 passed, 0 failed** in **3.96 seconds**. It is not an unexecuted requirement.
- Final focused normal-release/schema/PostgreSQL/retired-P1 suite after case-insensitive store replay hardening and source-pin refresh: **135 passed, 0 failed** in **59.82 seconds**. These overlap the broad run; do not add the totals as independent tests.
- Fresh-selection and existing label/retail targeted check: **52 passed**. No ordinary mutation test relies on overriding the semantic release decision; fake write flags and mocked transports are confined to tests.
- An intermediate run reused a persistent SQLite file and encountered two stale payment-fixture Draft Order page errors (`verified_at` absent). The fresh broad run passed; no Draft Order/template code was changed. A redundant restricted standalone rerun stalled and was stopped; its cases are covered by the completed broad run.
- Python compilation, `node --check static/retail.js` and `git diff --check` passed.

Reviewed final adapter SHA-256 (also pinned by the sealed read-only diagnostic):

```
c1bee08ac7a5c81bb763235506ad567d6ec450d3549ae8181f6af58ce7f7fc41
```

Migration required: **20261119_44**, following **20261118_43**, additive with nondestructive rollback. No migration or test command was run against production. Commit/branch and draft PR publication outcome are supplied in the delivery message; the repository contains no secrets or live credential exports.
