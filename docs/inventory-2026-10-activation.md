# Shopify inventory 2026-10 completion and release procedure

## Evidence and current release boundary

The owner supplied a successful authorized production read-only diagnostic for API 2026-10: `read_inventory=true`, `read_locations=true`, `write_inventory=false`, and location **Magammana West**, `gid://shopify/Location/92690219311`. This is read evidence, not mutation authorization or schema/output proof. The previous probe's `mutation_shape_verified=false` cannot establish the new mutation contract.

The official 2026-10 documentation has now been retrieved and reviewed. See [contract evidence](inventory-2026-10-evidence.md) and its source/hash manifest. `MUTATION_CONTRACT_VERIFIED=True` records that source review only; it does not enable writes. `SHOPIFY_INVENTORY_WRITE_ENABLED` remains default-off and was not changed in Railway. No live Shopify store requests/mutations, permission changes or deployment were performed here.

Ordinary receiving/adjustments use `inventoryAdjustQuantities` with an **available delta**, explicit fresh `changeFromQuantity`, and a stable non-Shopify ledger URI. Approved physical COUNT/OPENING reconciliation uses `inventorySetQuantities` on **on_hand** with the cutoff `changeFromQuantity`. Null CAS, obsolete compareQuantity and automatic retries are never sent. Both referenceDocumentUri inputs use **String**, not URI. `@idempotent` is on the mutation field with a non-null String key. The adapter requests the actual adjustment-group **id**, creation time, reason, reference URI and per-item/location changes.

Runtime introspection still must verify both complete input shapes, required types, unknown required arguments, directive location/key type, payload/error types, group/change outputs and filter arguments on the live shop. The owner-supplied earlier diagnostic does not establish these new checks. Native transfers remain unverified and pending. Full-store physical reconciliation remains deferred until POS completion.

## Preserve the existing app and all permissions

These are manual owner/admin steps, **not changes executed by Codex**:

1. Keep `SHOPIFY_INVENTORY_WRITE_ENABLED=false`. Do not touch the barcode flags, credentials, API/webhook versions, existing installation or products. Do not uninstall/recreate the app.
2. In the existing authorized Railway runtime, run the new read-only diagnostic before changing app access:

   ```sh
   python -m scripts.validate_inventory_readonly --include-contract > inventory-access-before.json
   ```

   Keep this JSON as the installed-scope baseline. It contains public schema metadata/scope handles and no tokens/secrets. Review `capabilities.installed_scope_handles`; do not infer the complete set from only read_inventory/read_locations booleans.
3. Identify the **same app whose client ID is used by ShopifyCatalogClient**, without publishing its client ID/secret in chat or logs. An authorized owner can match it privately in the existing Shopify Dev Dashboard/Shopify Admin app configuration.
4. For a Dev Dashboard-managed app, open that existing app, its configuration/version access-scopes section, and create the next configuration version. Copy the complete existing configured scope set, preserving every entry, then **add only `write_inventory`**. Release that configuration version to the same app. If this app is managed by Shopify CLI, edit only its existing app configuration's access-scopes list to the union of the current list and `write_inventory`, then use its normal approved app-configuration release process. Do not run these app-management commands from an unrelated checkout or create a second app.
5. In the live store's existing installation, follow the app's standard permission-update/re-authorization prompt as the store owner/authorized administrator. Approve the added inventory permission while retaining all prior permissions. For an older Admin-created custom app, use that same existing app's Admin API integration configuration, add write_inventory alongside all checked permissions, save and approve/update that installation; if Shopify requires credential replacement, stop and use the separately approved credential-management process rather than copying a token into chat or the repository. UI labels vary with app management type; the installed-scope API evidence is the final check, not merely a saved configuration checkbox.
6. The existing client uses cached client-credentials access tokens. A fresh diagnostic process obtains a fresh token through the existing connection. Do not manually replace credentials or paste tokens. The eventual approved application release/restart clears old in-process token caches; no temporary debug token endpoint is needed.
7. Run a fresh read-only verification, checking the original installed scopes are a subset of the new ones:

   ```sh
   python -m scripts.validate_inventory_readonly --include-contract --previous-scopes inventory-access-before.json > inventory-access-after.json
   ```

   Require `write_inventory=true`, `read_inventory=true`, `read_locations=true`, `mutation_shape_verified=true`, all `contract_checks` true, and `preserved_scope_check.missing_previous_scopes=[]`. If any previous scope disappeared, restore the original permissions before proceeding. A released app configuration without an updated installation/token is insufficient.
8. Verify a **known selected CRM variant** at Magammana West using its actual CRM ID (do not invent one):

   ```sh
   python -m scripts.validate_inventory_readonly --variant-id <KNOWN_CRM_VARIANT_ID> --location 'gid://shopify/Location/92690219311' --include-contract --previous-scopes inventory-access-before.json
   ```

   Match item/variant/location and separate on-hand/available observations to Shopify Admin. Save sanitized evidence. The script performs only fixed queries, uses a PostgreSQL READ ONLY transaction for local identity, and rejects both mutation operations and apply calls. It never authorizes a write, even when compatibility becomes ready.

## Approval contract migration and normal activation

Additive migration **20261119_46** (parent **20261119_45**) adds immutable `contract_version`. Existing documents remain NULL: earlier approval for an absolute operation never silently becomes approval for a new delta operation. Their historical references, baselines, plans, actors, attempts and outcomes remain intact. Old documents cannot acquire the new contract version through an update. Attempted/uncertain jobs keep their recovery path and must not be recreated to retry.

Before normal release, inspect pending old-contract documents. Never activate them implicitly. For a never-dispatched old draft/submitted/approved transaction, reject/review it and prepare a newly approved document that explicitly references the earlier record. Supplier invoice uniqueness remains enforced; use a clearly agreed corrected/revision reference with the original history linked in notes, not a silent duplicate invoice. For a DISPATCHING/UNCERTAIN/APPLIED_REVIEW document, investigate and reconcile first; do not create a replacement stock movement merely because its contract is older.

After this PR receives release approval, deploy the reviewed code/migration with the inventory write flag still false. Run the scope/schema checks above. `source_contract_verified` should be true; that fact alone is insufficient to release writes. Require all live schema checks, read/locations/write_inventory scopes, scope preservation and selected product/location observations to pass.

Only after the owner separately approves production-write activation may an authorized release operator enable **SHOPIFY_INVENTORY_WRITE_ENABLED**. Do not alter existing barcode flags or credentials. Before enabling, review every queued approval and every uncertain job; approvals expire after 30 minutes and workers revalidate active creator/administrator access. Enabling the normal feature is not permission to invent stock movements: only normally submitted, explicitly administrator-approved real business documents may dispatch. No special pilot subsystem or arbitrary test stock is required.

The new diagnostic supports `--require-write-ready`: after compatibility/source review, this exits nonzero if scopes, schema, source evidence or prior-scope preservation remain blocked. It always prints `write_authorized:false`. It neither changes a flag nor sends a mutation.

## Operational semantics and recovery

- Receiving and adjustments authorize a signed **delta**. Fresh preflight quantities may differ from the approval snapshot because legitimate Shopify stock movements occurred. The immutable approval remains unchanged; a durable DISPATCH_INTENT captures the fresh baseline, expected result, operation, reason, contract version, document URI and the original UUID request key. The delta API cannot restore old absolute balances or deduct an order a second time. Available CAS also prevents removing committed/reserved stock as an available adjustment; a negative delta larger than current available is rejected. Negative-result preflight rejects obviously invalid stock, but does not claim to lock other Shopify editors.
- Count/opening reconciliation authorizes an **absolute physical count**. Both cutoff quantities must still match at preflight. The count request includes explicit `changeFromQuantity` on each changed on-hand item/location; no null/bypass/obsolete compareQuantity is sent. A concurrent CAS conflict is quarantined, never resent blindly. Full-store physical opening reconciliation remains deferred until POS completion.
- `@idempotent` is attached to `inventoryAdjustQuantities(...)` / `inventorySetQuantities(...)`, not the operation definition. The stable document UUID key and exact query/variables envelope are persisted before network IO and never regenerated for retries. Shopify retains keys for 24 hours, not indefinitely; cached responses can be reconstructed from later records. The workflow still permits at most one attempt and has **no automatic retry**, regardless of Shopify replay support/retention. A fresh key must never be used to retry an uncertain event.
- The actual Shopify group ID, createdAt, reason, referenceDocumentUri and per-item/location changes are retained. Their identities, primary quantity, delta and ledger correlation must match the request. The response hash is supplemental evidence, not a substitute Shopify ID. The complete response is kept in the immutable REMOTE_ACCEPTED event and can be correlated with Shopify inventory history.
- Complete per-line remote read-back is mandatory. Any concurrent change, malformed/partial output, timeout, GraphQL error, unmatched group or read-back failure becomes UNCERTAIN/APPLIED_REVIEW as appropriate. Later inventory dispatch stays blocked pending manual reconciliation. Matching current balances alone cannot establish that a prior uncertain event succeeded.
- Movement reports distinguish approved snapshots from fresh execution expectations and actual read-back observations. The actual post-operation quantity can change again immediately; verified read-back is not a stock reservation or atomic global snapshot.
- Native transfers stay TRANSFER_PENDING. Existing order/Draft Order/import/barcode/webhook integration and POS stock authority remain unchanged. No receipt/adjustment automatically creates a sale deduction, transfer or supplier order.

## Rollback

Disable only the distinct inventory write flag through an approved operator action. Preserve all DISPATCHING/UNCERTAIN/APPLIED_REVIEW evidence and investigate Shopify history. Revert code through normal release procedures, retaining additive migration columns, contract fences, dispatch keys, audit events and manual reconciliation records. Downgrade intentionally erases nothing. Never reset attempts, change a document's contract, delete history or automatically reverse stock changes. A real correction requires its own explicitly approved normal business transaction.
