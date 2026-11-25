# Loyalty Phase 2B.2: final registration refinements

Baseline main: `5ea16fc4b7b7060933548fcae265593bca56f293` (merged PR #211). Branch: `feature/loyalty-phase2b2-final-refinements`. Previous migration: `20261125_58`; new migration: **`20261125_59`**.

## Behaviour

CRM lookup preselects a single unambiguous suggestion, displaying name, normalized mobile, membership/status and qualifying count. **A suggestion is not an association.** A separate hidden confirmed Customer ID is used by order payloads and pricing. Only successful explicit **Confirm & attach customer** or explicitly confirmed registration sets that ID. Multiple candidates are not preselected. HTTP/network/JSON/redirect failures retain Retry and never mean no match. Existing draft associations are retained; changing the purchaser clears confirmation, and stale responses cannot attach a different order. Server-side Customer ID/phone validation, permissions, pricing and frozen-order restrictions remain authoritative.

POS has a compact customer modal with clear search/register/save/anonymous actions. Name and valid mobile are required for registration, and operational-contact consent remains unticked and required by the existing server service. It is separate from marketing consent. Business invoice details remain available in collapsed Advanced invoice details. Anonymous invoice saving explicitly says no customer is attached; Continue as walk-in immediately clears the intended cart's customer while retaining entered invoice details. Changing a name/mobile clears the local selection until reselected or registered.

Current Sale shows the server-verified customer name/mobile, membership ID/status and qualifying count. It distinguishes active, pending, suspended/ineligible and unavailable membership. An unavailable membership lookup does not invent ACTIVE; known cart customer details remain visible. Change and Remove affect the active draft only, not the Customer registry or a finalized order. The notice “POS loyalty discounts are not yet enabled” changes no calculations.

The summary endpoint is scoped to an authorized, owned cart and returns six customer summary fields only. It does not expose purchase rows, profitability or exports. Cache is private/no-store. UI requests are fenced by cart ID, version, Customer ID and sequence; delayed replies cannot leak into a new cart. Held carts retain their own identity, while new/cancelled/completed carts do not transfer it to a new customer.

Display-only formatting uses Asia/Colombo and `Rs. 12,000.00`, preserving UTC storage and report input/filter semantics. Monetary strings are formatted without recalculating stored amounts. Product/loyalty rule inputs remain their original numeric values. Telephone country remains the numbering region, not physical location. Overseas purchaser and local recipient fields/mappings remain independent.

## Customer-name dependency audit

Repository searches covered Python services, routes, templates, migration/startup paths, local reporting, scripts and tests. Findings:

| Subsystem | Finding and correction |
|---|---|
| `models.Customer` | Legacy `unique=True` on name is replaced by a nonunique search index. IDs and phone metadata unchanged. |
| Shared registration | Global normalized-name veto removed; normalized-phone coordination, candidate conflict checks and actor/input-bound idempotency retained. Different phones may register identical names concurrently. |
| Admin profile correction | Name-only collision veto removed. Admin role, fingerprint CAS, phone conflict review and immutable audit retained. |
| Customer search/management | Already returns bounded lists and uses numeric IDs. Duplicate names display their own mobile/ID, never merge. |
| CRM and POS | Existing Customer ID FKs/frozen snapshots are authoritative. No name-based reassignment or financial rewrite. |
| Historical recovery | Global name-only CREATE veto removed. Name+phone matching remains a review signal among phone candidates; explicit IDs, approval/digest, source checks and shared phone mutex remain. No recovery is executed by this release. |
| Loyalty | Membership uniqueness is by Customer ID; reviewed attribution, identity fingerprints and shared-phone safeguards remain. Same-name customers receive independent memberships only after their own qualifying purchases. |
| Shopify | Bindings and readback use existing internal Customer ID/Shopify GID. No name-based identity key or new mutation added. |
| Accounting `entries.html` | Selector already submits numeric IDs; labels now include IDs so identical names are distinguishable. Journal posting logic unchanged. |
| Inventory/purchasing/POS payments/refunds/receipts | No authoritative customer-name key was found or changed. Supplier-name uniqueness is unrelated and remains intact. |
| Imports/exports/reporting | Existing local customer relations use IDs; name is display/search data. No customer name-key import/upsert was found in the inspected repository. External, unprovided integrations remain an operator check. |
| Startup | Calls `Base.metadata.create_all()` then Alembic upgrade to head. An old build unaware of revision 59 is **not** a valid rollback artifact. See below. |

No existing name is renamed, suffixed, merged or deleted. No global phone-uniqueness constraint is added; legitimate legacy shared-phone rows remain supported. Only the shared transactional service prevents accidental new phone duplicates; direct external database writers are outside this authorization boundary.

## Migration and rollback constraints

Revision 59 inspects and removes only single-column Customer-name unique constraints/indexes and preserves/adds a nonunique name index. All Customer IDs, phones, memberships, orders, payments, histories and FKs remain. It does not update rows or recover historical data. Repeated upgrade is safe. PostgreSQL migration tests cover legacy constraint names, linked identities/membership preservation, concurrent identical-name registrations and guarded downgrade.

Downgrade first checks for duplicate names, **before DDL**. If any exist it refuses; no rename, merge, deletion or forced survivor is allowed. If there are no duplicates, restoring uniqueness (a unique constraint on PostgreSQL, equivalent unique index without a table rebuild on SQLite) is technically supported only in a separately approved maintenance window after stopping writes. Production rollback should retain schema 59.

SQLite legacy unique constraints require a table rebuild. Migration refuses before DDL when foreign_keys is enabled; an offline backup and a connection with FK checking disabled before the migration transaction are required for that local/offline case. Re-enable checking and run `PRAGMA foreign_key_check` after the rebuild. Do not apply ad-hoc SQLite instructions to PostgreSQL production. Existing modern SQLite test databases already use the new metadata and need no legacy rebuild.

## Tests and screenshots

Final outcomes: **422 distinct tests passed**, including 22 new feature cases and 20 PostgreSQL-related cases; zero remaining failures, errors or skips. Overlapping runs are deduplicated. The initial SQLite rollback and old currency assertion failures are retained in the report with their passing affected-suite reruns. Exact final run counts and source evidence: `customer-final-refinements-test-results.json`. All automated writes occur in disposable SQLite/PostgreSQL with mocked Shopify/hardware. Browser tests use real Chromium at desktop and Android widths. Production and physical Android acceptance are separate.

Screenshots use synthetic data only:

- [Before CRM desktop](images/customer-final-refinements/before-crm-desktop.png), [before CRM Android](images/customer-final-refinements/before-crm-android.png)
- [After CRM desktop](images/customer-final-refinements/after-crm-desktop.png), [after CRM Android](images/customer-final-refinements/after-crm-android.png)
- [Before POS desktop](images/customer-final-refinements/before-pos-desktop.png), [before POS Android](images/customer-final-refinements/before-pos-android.png)
- [After POS modal desktop](images/customer-final-refinements/after-pos-modal-desktop.png), [Android](images/customer-final-refinements/after-pos-modal-android.png)
- [After Current Sale desktop](images/customer-final-refinements/after-pos-summary-desktop.png), [Android](images/customer-final-refinements/after-pos-summary-android.png)

Both before CRM and before POS states were reproduced from the actual PR #211 code using synthetic browser fixtures. The new CRM screenshot shows a suggestion before confirmation; it intentionally is not yet attached.

## Approved deployment procedure

1. Review the draft PR, dependency audit, migration and results. Back up the database; record current deployed commit, migration and approved historical baseline through existing read-only diagnostics. No production verification is claimed here.
2. Inventory actual single-column Customer-name constraints and custom indexes using read-only PostgreSQL metadata. Stop and review any unexpected custom name-based integration/constraint. Confirm no external writer depends on unique names. Check for lock-heavy transactions before a DDL maintenance window.
3. Prepare the **migration-aware rollback artifact** below before deploying schema 59. Do not assume the original PR #211 image will start against revision 59.
4. With separate release approval, apply `python -m alembic upgrade 20261125_59` through the normal migration process, then deploy the reviewed feature commit. No new environment variables, Shopify scopes or staff grants are required. Keep global loyalty discounts OFF and all hardware/Shopify safeguards unchanged.
5. Confirm runtime commit and migration 59, then reload browser tabs. New assets use `phase2b2-1`; verify the expected JS/CSS and updated template loaded.
6. Run the acceptance checklist below using existing legitimate profiles. Any actual registration requires normal business authorization; do not manufacture paid production transactions or recover historical orders for testing.

Read-only schema inspection (an authorized operator, database transaction read-only):

```sql
BEGIN READ ONLY;
SELECT version_num FROM alembic_version;
SELECT c.conname, pg_get_constraintdef(c.oid)
FROM pg_constraint c WHERE c.conrelid='customers'::regclass AND c.contype IN ('p','u');
SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() AND tablename='customers';
SELECT count(*) AS duplicate_name_groups FROM (SELECT name FROM customers GROUP BY name HAVING count(*)>1) AS groups;
ROLLBACK;
```

Do not paste customer names, phones, credentials or raw purchase details into logs/chat. Existing Phase 2B read-only diagnostic can verify selected membership/historical commercial hashes using operator-supplied IDs; those identifiers are not feature logic or test fixtures.

## Safe application rollback (retain schema 59)

Stop new registration actions during rollback. Build/review a compatibility release from the previous application source **including revision 59's migration file**, so startup Alembic recognizes the current database version. Example in a separate developer checkout, with real approved feature SHA substituted:

```sh
git switch -c rollback/phase2b2-compatible 5ea16fc4b7b7060933548fcae265593bca56f293
git restore --source=<approved-phase2b2-commit> -- alembic/versions/20261125_59_customer_names_nonunique.py
```

Review, test and publish that rollback build through normal owner approval. Its Alembic head remains 59; do not stamp or downgrade the database. Legacy ORM uniqueness metadata does not alter an existing table via create_all (covered by PostgreSQL test); older registration/profile screens may temporarily reject identical names, but existing IDs/history remain intact. Restart only after separate deployment approval. Keep Customer rows, registration keys, phone mutexes, memberships, audits, financial data and historical decisions unchanged.

If uniqueness restoration is ever requested, first stop all writers and obtain a fresh duplicate-name report. A blocked downgrade is a safety result, not a reason to manually alter records. Any reconciliation or destructive procedure requires separate owner approval. No such procedure is supplied or executed here.

## Acceptance checklist

- Existing accepted memberships and the first historical recovery remain intact; leave the remaining historical orders untouched. Programme OFF remains OFF.
- CRM unique lookup visibly suggests one profile but confirmed Customer ID stays empty until Confirm & attach. Multi-match selection starts empty and requires explicit confirmation. Retry does not mean no match.
- Sri Lankan and overseas purchaser mobiles remain normalized; local recipient contact and Shopify shipping remain separate.
- Legitimate same-name customers with different mobiles have separate IDs; exact-phone repeat/retry is rejected or idempotent, not merged. Same-name membership histories remain independent.
- POS search/register works for authorized cashier, consent is unticked and correctly sized, Advanced invoice details still works, walk-in invoice saving never claims attachment.
- Current Sale shows verified membership states and unavailable states honestly, with no POS loyalty discount. POS-only staff cannot read another cashier's cart summary or export full history through this endpoint.
- Change/remove affects only the intended draft. Cancel/new/checkout reset identity; held-cart resume restores its own identity. Refresh and delayed responses never transfer identity to another cart.
- Display dates are Sri Lankan time and amounts formatted as Rs.; stored totals and UTC/report filters remain unchanged.
- Existing barcode, stock, purchasing, payment, journal, Shopify verification, receipt and drawer workflows remain under their existing controls. Do not trigger hardware or artificial financial activity for this UI acceptance.
- Repeat relevant flows on real Android and shop desktop after approval. No physical-device or production acceptance is claimed by automated screenshots.

## Remaining limitations

Production runtime, actual constraint metadata, external integrations, retained historical state and real Android behaviour were not accessible here. No new privilege grants, production migrations, financial/hardware mutations, Shopify changes, marketing consent, notifications, historical recovery, merge or deployment are performed by this implementation.
