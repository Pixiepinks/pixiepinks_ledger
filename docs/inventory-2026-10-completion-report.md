# Inventory 2026-10 adapter completion report

Branch: `feature/inventory-2026-10-completion`, based on main `cac7827` (merged Phase 3 PR #176).

## Result

Official 2026-10 documentation is reviewed and the ordinary inventory adapter is completed against that contract. Source review is now verified; **live inventory writes are not enabled**. Separate release approval, fresh production scope/schema evidence and the write flag are still required. No live store requests/mutations, permission changes, Railway configuration changes or deployment were performed.

The owner supplied read-only live evidence: API 2026-10, read_inventory/read_locations true, write_inventory false, Magammana West location `gid://shopify/Location/92690219311`. That prior run did not establish this corrected mutation schema. The new diagnostic must be run after reviewed deployment before enabling normal operations.

## Corrected behavior

- Receiving/ordinary signed adjustments use `inventoryAdjustQuantities` with available **delta**, fresh explicit `changeFromQuantity`, and a deterministic non-Shopify ledgerDocumentUri per line. Legitimate changes before preflight do not cause an old absolute balance to be restored. Negative adjustments cannot remove stock already committed/reserved out of available.
- Approved physical COUNT/OPENING reconciliation uses `inventorySetQuantities` on on_hand, with the frozen cutoff `changeFromQuantity`. No null CAS or obsolete compareQuantity/ignore bypass is sent.
- Reference URI fields use **String**, not URI. Exact input field/nullability, required unknown fields/arguments, both mutation payloads/error objects, field-level idempotency and actual group/change/filter types are probed read-only before dispatch.
- `@idempotent(key:$key)` is on the mutation field, with `$key:String!`. The original durable UUID, exact query/variables envelope, operation/reason, quantity name, plan and SHA-256 are committed before network IO. There is no automatic retry, replacement key or attempt reset. Shopify's 24-hour retention is not treated as permanent deduplication.
- Actual Shopify adjustment-group **id**, timestamp, reason, business reference and per-item/location changes are validated and stored. A response fingerprint is supplemental evidence only. No synthetic GID substitutes for actual identity.
- Complete read-back, uncertainty quarantine, active administrator/creator permissions, immutable history and manual reconciliation remain intact. Count CAS races and delta CAS races never trigger a blind resend. Read-back differences remain reviewable outcomes, not automatically repeated stock movements.
- Movement reports distinguish immutable approved snapshots from fresh execution expectations and actual read-back quantities.
- Native transfers remain pending. Full-store opening reconciliation remains deferred until POS completion. Existing barcodes, prices, imports, Draft Orders, order lifecycle and webhooks are unchanged.

## Migration and approval fence

**20261119_46**, parent **20261119_45**, adds immutable `contract_version`. It preserves old documents as NULL rather than silently upgrading their existing approval to new semantics. Old approvals cannot dispatch under the new contract, and immutable payload/baseline/plan/history remains intact. Review/reject never-dispatched old documents and explicitly prepare a corrected/reference-linked normal document when needed; uncertain/attempted documents require reconciliation first. No destructive downgrade or stock backfill.

## Files

- `inventory_shopify.py`: corrected operations, official reason mapping, CAS/ledger/idempotent inputs, full runtime contract checks and group response validation.
- `inventory_service.py`: frozen contract approvals, fresh delta execution plan, exact durable wire envelope, read-back and movement reporting.
- `inventory_models.py`, `alembic/versions/20261119_46_inventory_contract.py`: additive contract identity/fence.
- `scripts/validate_inventory_readonly.py`: schema evidence, explicit per-check blockers, installed scope snapshot, prior-scope preservation check, optional compatibility-required exit status; cannot mutate.
- `static/inventory.js`: approved delta versus absolute count labels.
- `tests/inventory_contract_fixture.py`, `tests/test_inventory_adapter.py`, `tests/test_inventory_phase3.py`, `tests/test_inventory_postgres.py`: documentation-backed fixtures, new adapter/approval/migration/concurrency regressions.
- `docs/inventory-2026-10-evidence.md`, `docs/evidence/inventory-2026-10-contract-review.json`: reviewed source URLs, hashes and confirmed semantics.
- `docs/inventory-2026-10-activation.md`: exact existing-app scope union/preservation, diagnostic, queued-document, activation and rollback procedure.
- Earlier Phase 3 guide/deployment/report documents point to the superseding contract and preserve historical proposal context.

## Tests

Final relevant suite: **418 passed, 1 skipped** with disposable SQLite plus isolated localhost PostgreSQL schemas. It covers inventory adapter/service/SQL fences/worker concurrency/browser, scanner/Node controller, Phase 1 labels/product filtering, barcode adapter/sync/production/pilot, Shopify catalog/Draft Order/order lifecycle, WhatsApp quick messages and customer-question automation. All Shopify store HTTP responses/mutations were mocked.

```sh
python -m pytest -q -rs --disable-warnings \
 tests/test_inventory_phase3.py tests/test_inventory_adapter.py \
 tests/test_inventory_postgres.py tests/test_inventory_browser.py \
 tests/test_retail_scanner.py tests/test_retail_scanner_browser.py \
 tests/test_retail_phase1.py tests/test_retail_product_filters.py tests/test_retail_postgres.py \
 tests/test_retail_barcode_adapter.py tests/test_retail_barcode_sync.py \
 tests/test_retail_barcode_sync_postgres.py tests/test_retail_barcode_production.py \
 tests/test_retail_p1_pilot.py tests/test_shopify_catalog.py \
 tests/test_shopify_draft_order_sync.py tests/test_shopify_order_lifecycle.py \
 tests/test_whatsapp_quick_messages.py tests/test_customer_questions.py
```

The single SQLite-skipped Draft Order retry-concurrency case was executed separately with its primary database configured as isolated PostgreSQL. That case and an explicit diagnostic rejection test for both SET and ADJUST mutations passed: **2 passed**. No final failures. Python compilation, Node syntax and `git diff --check` passed. Official documentation and GitHub API access were verified after the owner's Codex-only network approval. No physical hardware or live mutation test is claimed.

## Remaining release requirements

1. Review and merge/deploy separately with `SHOPIFY_INVENTORY_WRITE_ENABLED=false`.
2. Run `python -m scripts.validate_inventory_readonly --include-contract` inside the authorized Railway runtime, preserving the complete installed-scope baseline.
3. Owner/admin adds **only write_inventory** to the existing app's complete scope set and approves the updated installation; no new app/credentials or scope replacement. Re-run with `--previous-scopes` and require zero lost scopes. See the activation guide for app-management variants and cached-token handling.
4. Require all corrected live schema checks, installed inventory/location scopes, selected identity/quantity observations and operator review to pass. `--require-write-ready` is a compatibility gate only and always reports `write_authorized:false`.
5. Review old-contract/queued/uncertain documents before separately approved write activation. Normal explicitly submitted and administrator-approved real transactions provide live operational verification; no arbitrary stock or special pilot mechanism.

The code is prepared for normal operations, but this task does not authorize changing production write variables or executing any stock-changing transaction.
