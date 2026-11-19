# Official Shopify 2026-10 inventory contract review

Reviewed 19 November 2026. Documentation requests returned HTTP 200 after the owner approved Codex access to shopify.dev and api.github.com. [Evidence manifest](evidence/inventory-2026-10-contract-review.json) records source URLs and response SHA-256 hashes. This is a documentation review, not a live-store mutation test. No Shopify permissions, inventory or Railway settings were changed.

## Inputs and concurrency

[InventoryAdjustQuantitiesInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/InventoryAdjustQuantitiesInput) declares `changes: [InventoryChangeInput!]!`, `name: String!`, `reason: String!` and `referenceDocumentUri: String`. [InventorySetQuantitiesInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/InventorySetQuantitiesInput) uses `quantities: [InventoryQuantityInput!]!` and the same scalar fields. The reference URI's GraphQL scalar is **String**, not URI.

[InventoryChangeInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/InventoryChangeInput) declares `delta: Int!`, item/location `ID!`, `changeFromQuantity: Int` and `ledgerDocumentUri: String`. [InventoryQuantityInput](https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/InventoryQuantityInput) declares `quantity: Int!`, item/location `ID!`, and `changeFromQuantity: Int`.

Despite nullable schema types, both CAS fields must explicitly be present: Shopify documents, “This field is mandatory: you must explicitly pass in a value”. Null opts out; this adapter never supplies null. A stale value produces `CHANGE_FROM_QUANTITY_STALE`. `compareQuantity` is the pre-2026-04 interface and is not sent.

The [adjust mutation](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/inventoryAdjustQuantities) example adjusts `available` and its response shows corresponding available and on-hand deltas. Ordinary receiving/adjustments use this supported delta operation, comparing the fresh available value. [Set](https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/inventorySetQuantities) explicitly supports available/on_hand but is intended for a source of truth. Here its use is limited to an administrator-authorized physical COUNT/OPENING document, with cutoff validation and explicit on-hand CAS. Routine receipts do not overwrite an absolute balance.

Ledger URIs identify the individual movement, distinct from the business reference URI. Shopify requires a non-Shopify ledger URI for quantity names other than available. The adapter includes a stable `gid://pixiepinks/InventoryMovement/<persisted-UUID>-<line-index>` even for available adjustments; this improves audit traceability and stays identical for the same request.

## Output and identity

[InventoryAdjustmentGroup](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/InventoryAdjustmentGroup) **does** expose `id: ID!`, `createdAt: DateTime!`, `reason: String!`, `referenceDocumentUri: String` and `changes: [InventoryChange!]!`. Its `changes` field accepts optional quantityNames/item/location filters. [InventoryChange](https://shopify.dev/docs/api/admin-graphql/2026-10/objects/InventoryChange) exposes name, delta, quantityAfterChange, item, location and ledgerDocumentUri.

The adapter requests and retains the actual Shopify group ID and complete requested evidence. It validates reference/reason/time, primary quantity, exact item/location membership, delta, duplicate changes and ledger correlation. It does not invent a GID from a hash. A response hash is supplemental immutable audit evidence only. Complete fresh per-line read-back remains required, because response values and later quantities can differ under concurrency.

## Idempotency

The mutation examples attach `@idempotent(key: $key)` to the **mutation field**, with `$key: String!`; not to the operation definition. Keys became mandatory for these operations in 2026-04.

The [implementation guide](https://shopify.dev/docs/apps/build/apis/graphql-admin/implementing-idempotency) states that Shopify retains keys for **24 hours**, returns `IDEMPOTENCY_CONCURRENT_REQUEST` when a duplicate is in progress, and caches successful responses. It also warns cached responses can be reconstructed from records that have since changed. After 24 hours a retry can become a new operation. Therefore the CRM keeps its permanent one-attempt fence and no automatic retry, rather than treating Shopify idempotency as indefinite deduplication.

The original UUID, complete query/variables envelope, hash and dispatch intent are committed before IO. Neither a CAS failure, timeout nor replay error generates a replacement key. Uncertain events quarantine the shop for administrator reconciliation. Matching current stock alone does not prove a previous mutation succeeded.

## Reasons and permissions

The [inventory management guide](https://shopify.dev/docs/apps/build/orders-fulfillment/inventory-management-apps/manage-quantities-states) lists received, damaged, shrinkage, restock and correction among supported reasons. The closed mapping is RECEIPT→received, DAMAGED→damaged, LOST→shrinkage, RETURN→restock, and general corrections/manual adjustments/COUNT/OPENING→correction. Reasons are String fields, so introspection cannot validate their semantic value; the documentation establishes this mapping.

Mutation pages require `write_inventory` and appropriate user authority. The owner-provided live read-only evidence confirms read_inventory/read_locations but **not write_inventory**. Before release, the new production diagnostic must verify actual mutation/schema output types and scopes on the existing installation. Source review sets `MUTATION_CONTRACT_VERIFIED=True`; this means only that the implemented contract has reviewed authoritative documentation. It neither grants permissions nor enables `SHOPIFY_INVENTORY_WRITE_ENABLED`, approves a transaction, deploys code or authorizes an inventory write.

Native transfers remain separately unverified and pending. Full-store opening reconciliation remains deferred until POS completion.
