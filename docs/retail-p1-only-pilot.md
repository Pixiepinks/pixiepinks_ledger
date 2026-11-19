# P1-only barcode pilot: implementation and manual procedure

2026-11-19 (Asia/Colombo). Base main: `d842d3e`.

**Prepared for review only. No live Shopify request or mutation, deployment, merge, Railway variable change, scope change or production database change was performed during implementation. No approval to execute the pilot has been given.** Ordinary `MUTATION_SEMANTICS_VERIFIED=False`, `SHOPIFY_PRODUCT_WRITE_ENABLED=false` and `RETAIL_BARCODE_SYNC_ENABLED=false` remain mandatory throughout this pilot, including future execution. There is no general-purpose gate bypass.

## Fixed identity and prior evidence

| Field | Required value |
|---|---|
| Store | `eb2b70.myshopify.com` |
| Product | Kids Projector Torch - Pink |
| SKU | `1001200` |
| Internal code | `P1` |
| Immutable allocation ID | `1` |
| Reviewed local variant/allocation version | `2` |
| Product GID | `gid://shopify/Product/9055242223919` |
| Variant GID | `gid://shopify/ProductVariant/47281619206447` |

The owner reports a successful read-only scan of 2,259 accessible variants, stable membership, zero P1 collisions and a completely empty selected barcode collection. That is historical owner-supplied evidence, not permanent authorization. Both preparation and execution perform a new full scan; execution never trusts the earlier report or the preparation scan alone.

## Authorization and one-attempt boundary

The mechanism is available only through four additive endpoints under the existing authenticated Retail router:

- `GET /crm/retail/api/barcode-p1-pilot`: current durable status; no mutation or preparation.
- `POST .../prepare`: admin review, fresh read-only Shopify checks, and local audit preparation only.
- `POST .../execute`: **the only write-capable operation**, requiring separate explicit approval below.
- `POST .../reconcile`: Shopify reads and local audit updates only; never dispatches a mutation.

All POSTs use existing session authentication and CSRF protection. The actor comes from the authenticated session, never a request field. An active `ADMIN` is required in addition to the existing retail permission check; a delegated non-admin cannot use this exception. Preparation returns a random 256-bit nonce, bound by its hash and a digest to the same approver, exact identity, protected baseline and a ten-minute expiry. Refreshing a still-unapproved preparation rotates the nonce. Status/history never disclose the plaintext nonce.

No endpoint accepts a variant, product, barcode, SKU, allocation, version, actor, job ID, batch, arbitrary GraphQL operation or arbitrary mutation payload. Unknown fields are rejected. The separate remote method submits a fixed one-variant payload; it cannot be used as the ordinary barcode worker's write adapter. Private Python helpers/permits are implementation details, not an operator interface; do not call them directly or patch constants.

A fixed unique job token and item idempotency key reserve this one pilot for the lifetime of the retained database records. Existing Phase 1B job/item/event/reservation tables are reused with `PILOT_*` statuses, which ordinary workers never claim. No new schema migration is needed; Phase 1B migration `20261118_43` must already be installed. There is no attempt to manipulate a Shopify idempotency header that this mutation does not support.

Execution requires the exact typed phrase:

```text
WRITE P1 ONLY TO eb2b70.myshopify.com PRODUCT 9055242223919 VARIANT 47281619206447 SKU 1001200 ALLOCATION 1 VERSION 2
```

The service rechecks live guards, scope/schema/shop, local ledger/variant identity, current permissions, expiry/digest and queue conditions. It holds the existing PostgreSQL shop-row lock and shared locks on the approver, variant and ledger across fresh checks and the remote attempt. Shared locks permit the independent audit connection's foreign-key checks while blocking local edits. PostgreSQL READ COMMITTED is required; SQLite and other isolation modes fail closed. Local lock waits have a five-second limit.

Separate database transactions commit `PILOT_APPROVED` and then **`PILOT_INTENT`, attempts=1, sent_at** before crossing the mutation boundary, while the lock-holder stays open. The final target read must be at most five seconds old at dispatch; the deadline is checked again after any authentication refresh. The exact HTTP mutation is attempted at most once, without automatic retry or a generic retrying mutation client. A timeout, process death, lost response, HTTP/GraphQL error or database failure after intent never rearms the pilot. A crash before the POST may therefore consume the one attempt without sending it; this deliberately sacrifices retry convenience for safety. Preparation cannot reset an approved, blocked, attempted or uncertain pilot.

The existing flags stay false even for pilot execution; ordinary synchronization still refuses. A source-level semantic gate or flag unexpectedly becoming enabled blocks the pilot instead of enabling other work. Pilot authority is the authenticated, nonce-bound, exact-phrase request for this one identity, not an environment toggle.

## Queue fencing, remote preflight and protected comparisons

Preparation and execution refuse competing queued/running jobs, checking/sent/unknown/conflict items (including uncertain items whose parent says COMPLETE), and reservations. These checks are conservative across the barcode tables and exclude only this pilot's own records. They are repeated under the shop lock immediately before durable intent. Nothing is auto-canceled or force-cleared. The ordinary worker ignores `PILOT_*` statuses and the ordinary source gate remains unchanged.

The existing authenticated `BarcodeClient` is reused at explicit API 2026-10. Full unfiltered accessible-catalog pagination, all secondary barcode pages (20 values per barcode page), catalog ID re-enumeration and target reread remain inherited from the reviewed adapter. Existing manufacturer/supplier values or any collision block execution. The minimal mutation is exactly:

```json
{"productId":"gid://shopify/Product/9055242223919","variants":[{"id":"gid://shopify/ProductVariant/47281619206447","barcodes":[{"value":"P1"}]}]}
```

The existing fixed `productVariantsBulkUpdate` document sets `allowPartialUpdates:false`. No regulated barcode type or legacy barcode input is supplied. No SKU, price, inventory, title, image, option or other unrelated field is submitted.

A separate fixed read captures and compares SKU, price, compare-at price, tax fields, position, inventory policy/aggregate quantity, selected options, variant image, inventory-item identity/SKU/tracking/shipping fields, inventory levels and location IDs, product title/handle/status/description/vendor/type/tags/options/featured image and product images. Inventory levels include available, on_hand, committed, incoming, reserved, damaged, safety_stock and quality_control quantities. Level and image connections are exhausted with 20-item pages, a 100-page/60-second cooperative budget, identity/progress/duplicate checks and strict missing-field rejection. Query/permission failures block; no scopes are added. Protected values are compared before and after the fresh preflight scan, and against preparation. A final complete target read is the last Shopify read before durable intent and dispatch.

After any mutation response, including uncertainty, the service performs complete barcode read-back/collision inspection and protected-attribute comparison. It records `PILOT_VERIFIED` only for **exactly one P1 value with null type**, no other collision, matching identity, unchanged local allocation and matching protected baseline. It then rereads the target once more. Mere mutation acceptance is never verification. Protected-field changes, typed/extra barcodes, unreadable state or mismatches yield `PILOT_UNKNOWN`, retain the reservation and never trigger another mutation. Audit events preserve review baselines/digests, exact approval and operator, intent, uncertainty and verification. Retail audit presentation recognizes `PILOT_VERIFIED`; local identifiers and imported barcode values themselves are never updated by this mechanism.

The protected query covers the explicitly listed attributes and accessible inventory levels/images, not every possible Shopify metafield, hidden field or app-owned resource. Automatic `updatedAt` timestamps are intentionally excluded because a barcode mutation can change them. Minimal payload construction is the primary guarantee that this client submits no unrelated modifications; before/after reads provide additional evidence for the protected set. Permissions and API field compatibility for this **new protected query have not yet been verified live**; preparation will block on any incompatibility. Do not describe mocks as that live verification.

## Exact manual runbook

### 1. Review and release preparation — no write approval implied

Review this branch, tests, payload and protected query. Deploy only through a separately approved normal release; this implementation does not merge or deploy itself. Keep the ordinary source gate and both existing write flags false. Do not change global Shopify API version, webhook version, authentication, scopes, Draft Orders or imports. The underlying adapter hash remains `180dc2501e3dbb262133ffb682f5c56fa7573830afe25914fef7f7223b7d0f93`.

Choose a quiet window and name the responsible administrator. Identify and pause all competing **barcode writers**: manual Shopify barcode edits, bulk/CSV updates, supplier apps, scheduled barcode pushes and other integration writers. Confirm the pause with their owners, not merely by observing an empty CRM queue. Pause only barcode-specific schedules/consumers if separable; do not stop shared WhatsApp/Draft Order workers or the CRM. Ordinary barcode writes remain blocked by the existing gates. Avoid starting new local allocations/import commits during the short window; the shop lock also serializes their commits without changing import logic.

Check existing barcode history and resolve known queued or uncertain work through a separately reviewed supported workflow before starting. Do not delete reservations, force statuses to COMPLETE, delete pilot history or enable write flags to make preparation pass. If existing controls cannot safely resolve an old job, stop and review that blocker. Preserve all audit records.

The update is **not atomic compare-and-swap**. A barcode added after the final empty read could be replaced by Shopify's replacement-set mutation, and a late competing P1 assignment elsewhere could introduce a collision. Catalog membership checks do not prevent edits or provide a snapshot. Execution is permissible only after competing writers are paused and the owner explicitly accepts the remaining risk. Ordinary inventory/sales activity can also change protected quantities and cause an unverified result; never restore inventory to an old baseline to force verification.

### 2. Prepare and inspect — Shopify reads plus local audit records only

Sign into the CRM as the responsible active ADMIN. Open `/crm/retail/products` in that authenticated browser. Use this reviewed helper in the browser console; it uses the existing session and CSRF token without exporting credentials:

```js
window.p1Request = async function (action, payload) {
  const csrf = document.getElementById('retail')?.dataset.csrf;
  if (!csrf) throw new Error('Open the authenticated Retail Product Master first');
  const response = await fetch('/crm/retail/api/barcode-p1-pilot' + (action ? '/' + action : ''), {
    method: payload === undefined ? 'GET' : 'POST',
    credentials: 'same-origin', cache: 'no-store',
    headers: payload === undefined ? {} : {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
    body: payload === undefined ? undefined : JSON.stringify(payload)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'P1 action blocked');
  return result;
};
```

Inspect status without preparing or dispatching:

```js
console.log(await p1Request(''));
```

Only after confirming the writer pause and risk review, prepare the review:

```js
window.p1Review = await p1Request('prepare', {
  editors_paused: confirm('Have ALL competing barcode editors and integrations been paused?'),
  risk_accepted: confirm('Have you reviewed and accepted the remaining non-atomic update risk?')
});
console.log({...p1Review, nonce: '[held in this browser only]'});
```

Require `PILOT_PREPARED`, attempts 0, the exact identity table above and a ten-minute expiry. Review the retained protected baseline and audit events through existing administrator barcode history/CSV; these contain product/inventory data, not credentials. The returned nonce is held only in this browser; do not paste it or session/CSRF tokens into chat or logs. Preparation alone **cannot** mutate Shopify. A blocked preparation means stop and investigate; do not weaken field validation, change version 2, overwrite a supplier barcode or enlarge a target list.

### 3. STOP: obtain the owner's separate authorization

**Do not run the execution block below under the current implementation request.** Obtain a new explicit approval naming **P1, allocation 1/version 2, SKU 1001200 and variant `gid://shopify/ProductVariant/47281619206447` in product `gid://shopify/Product/9055242223919`, store `eb2b70.myshopify.com`**. Confirm the quiet window and exact prepared baseline remain appropriate. If preparation expires, a still-unapproved review may be refreshed by the same admin; old nonces become invalid. If the allocation/version changes, stop for a new reviewed implementation/authorization rather than changing the hardcoded identity.

### 4. Future separately authorized one-time execution only

Following that separate owner approval, in the same admin browser, run **once**:

```js
const p1Typed = prompt('Type the exact confirmation phrase from the reviewed P1 preparation.');
if (!window.p1Review || p1Typed !== p1Review.confirmation) throw new Error('Exact P1 confirmation required');
window.p1Result = await p1Request('execute', {
  nonce: p1Review.nonce, confirmation: p1Typed,
  editors_paused: confirm('Are ALL competing barcode writers STILL paused?'),
  risk_accepted: confirm('Do you explicitly approve this ONE P1 update and accept the remaining race risk?')
});
console.log(p1Result);
```

The exact phrase is printed earlier in this document. This request, not preparation or deployment, is the explicit operator approval recorded in the audit. The server performs a **new** live preflight and can still block. Do not use generic batch approval, enable global flags or submit a second execute request as a retry.

### 5. Verify or reconcile; never resend

If the request returns `PILOT_VERIFIED`, require attempts 1, an exact P1/null-type read-back, `protected_unchanged=true`, the protected SHA and verification timestamp; review the immutable events. Confirm SKU/price and the protected inventory/product evidence in the report. A report's `write_authorized=false` means there is no reusable authority for another update.

If the browser/proxy times out, the server process dies or the outcome is `PILOT_UNKNOWN`, inspect status first:

```js
console.log(await p1Request(''));
```

When an attempt intent exists, an active administrator can request **read-only Shopify reconciliation** (with local audit updates):

```js
console.log(await p1Request('reconcile', {}));
```

This endpoint never calls the mutation path, even if Shopify is empty. Unchanged P1/null-type plus the full protected comparison is required to resolve to verified. A changed local allocation/version, post-write inventory change or unsupported protected field can keep the outcome unknown pending manual review. Do not reconcile an already-verified historical result just to refresh its timestamp after normal imports have advanced its local version. If there is approval but no dispatch intent, no automatic resend or rearming is provided; stop for a reviewed recovery decision. Preserve the prepared/intent/audit records.

Do not remove a supplier barcode, clear P1, modify stock/prices, reset attempt counters, delete jobs or replay a mutation as compensation. Any corrective Shopify write needs separate authorization. Once the exact outcome is reviewed, end the quiet window and let the existing read-only Shopify import observe P1 normally; no import logic has changed. Keep global gates disabled and stop before any P2/second product.

## Rollback and remaining blockers

Code rollback means reverting this feature deployment or removing the pilot routes/module through normal review. Retain all job/item/event/reservation records, especially durable intent; never roll the audit database back to an earlier approval state to rearm a pilot. Reverting code does not undo a Shopify barcode and must not send any compensating product mutation. Older UI code may not recognize `PILOT_VERIFIED`, but the preserved audit and normal imports remain authoritative observations.

Remaining before any live execution: approved release; live protected-query schema/permission checks during preparation; fresh empty-state/full-catalog evidence; a confirmed writer pause/queue fence; review of the protected baseline; and the separate exact owner/operator authorization. No isolated development-store mutation or live production pilot was executed in this task. Complete accessible-catalog inspection is not proof about inaccessible records or concurrent edits.

## Validation and files

Changed files: `retail_barcode_p1_remote.py`, `retail_barcode_p1_pilot.py`, `retail_routes.py`, `retail_service.py`, `tests/test_retail_p1_pilot.py` and this runbook. No migration, source-gate change, adapter hash change or environment flag change.

Final automated results are appended after verification. Tests use actual disposable local PostgreSQL for locks/durable commits and only `httpx.MockTransport` for Shopify. No mutation test overrides the ordinary source gate or either write flag. Existing test cases that do so are deliberately deselected.

- Final selected regression run: **295 passed, 0 failed, 1 skipped, 9 deselected** in 101.67 seconds. Includes PostgreSQL pilot/diagnostic tests, pagination/schema/adapter guards, retail imports/labels/filters, Shopify catalog, Draft Orders and WhatsApp quick messages.
- The skipped PostgreSQL-only Draft Order two-session claim case was separately run in a disposable local schema: **1 passed, 0 failed** in 4.33 seconds. It is not an unexecuted test.
- Pilot-focused suite: **40 passed** using real disposable PostgreSQL and mocked Shopify. Covers exact identity/P2 rejection, competing work on a different variant, concurrency, nonce expiry/rotation/tampering, current approver roles, CSRF/target-field rejection, durable intent after process death, one-attempt timeout recovery, protected read/write comparison, token refresh exceeding the final-target deadline, paginated inventory/images and missing/denied/invalid query data.
- Nine existing write-enabling/source-gate-override test cases were intentionally deselected, not counted as passing. No test in this task enabled either write flag or changed the ordinary semantic source gate.
- `git diff --check` and Python compilation passed. No migration is added; existing Phase 1B tables and durable audit conventions are reused.
