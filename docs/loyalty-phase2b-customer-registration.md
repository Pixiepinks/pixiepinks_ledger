# Loyalty Phase 2B: centralized registration

## Scope and architecture

Based on main `b760497df6478e948b541dd84e46c64340f8b5c9` (recipient separation). CRM, POS and Customers now use the existing Customer table and shared `customer_registration.py` service. No second customer or loyalty engine is introduced. Customer selection remains explicit; phone matches are suggestions, never automatic associations. Customer WhatsApp numbers remain independent of delivery-recipient numbers.

Registration accepts a name and complete mobile, normalizes Sri Lankan and international numbers, records source/UTC time, and does not grant marketing consent. Display dates use Asia/Colombo. Registering, searching, opening a conversation or saving an inquiry/draft does not activate membership. The existing transactional loyalty outbox creates a member after a qualifying verified positive purchase, even while the discount programme is OFF. Existing members are recalculated normally on refunds/cancellation. POS loyalty discounts remain unavailable.

Registration requests carry durable actor/input-bound idempotency keys. A database phone mutex serializes central registration and separately approved historical creation. Existing shared-phone Customers are retained; ambiguous identity and existing-name conflicts require explicit selection or administrator review. The existing unique Customer name constraint remains: two people with the same name cannot be silently created or merged. No fabricated suffixes or phone-only order links are added.

New interactive CRM orders require explicit Customer ID or a recorded unidentified reason; unidentified orders receive no loyalty discount. Trusted existing service/automation entry points retain compatibility and record a service-created unidentified reason. Anonymous POS checkout remains supported. POS registration saves selection to the originating cart, and delayed responses cannot attach a Customer to another cart. A newly started cart does not inherit the previous customer.

Customer profile edits require administrator role and a matching current fingerprint. They do not rewrite frozen historical order details, Customer associations or monetary snapshots. Deletion through the Customers screen is blocked in favour of identity review. No profile merge feature is introduced.

## Access and audit

CRM registration reuses sales-order editing permissions; POS registration reuses `pos.sell`; Customers uses the existing customers permission. APIs enforce the requested channel, authentication and CSRF for mutations. Sensitive identity correction is admin-only. POS-only staff do not gain cross-channel purchase-history access or exports. History responses are bounded to 20 summaries. Search responses are limited to 20, with a continuation warning. Personal responses use private/no-store caching.

In Staff & Access, retain WhatsApp Inbox access for CRM users and grant the existing Sales Orders section plus its create/edit action permissions for registration in orders. Cashiers use the existing POS: view / POS: sell grants (`pos.view`, `pos.sell`) and their existing register permissions. Grant Customers (`customers`) only to staff who need that management screen. Loyalty: View members (`loyalty.members.view`) controls full member navigation. Do not grant historical migration execution, programme enablement, profitability or report export just to permit registration. No permissions are automatically added by the migration.

Existing immutable loyalty audits record registration, conflicts, profile corrections and order association/unidentified reasons. Marketing opt-in, WhatsApp sending, Shopify customer creation, stock, journals, payments and automatic printing are not registration side effects. Existing independently authorized consent mechanisms remain intact.

## Migration and preserved history

Revision `20261125_58`, parent `20261124_57`:

- Nullable Customer normalized_mobile, registration_source and registered_at; nonunique normalized-mobile index.
- Customer phone coordination table and durable registration request table.
- Nullable SalesOrder unidentified_reason.

There is no legacy row backfill or automatic historical association. Existing source/date values are displayed as unrecorded, not invented. Existing member IDs, purchase counts and balances remain unchanged by migration. The separately approved Phase 2A historical tool only shares the mutex; all its approval/digest and suppression controls remain.

Owner baseline for **operator verification only**: Customer 2 / order 17 (`SO-20261124-19AA03E0`), membership `PP-L-000001`, ACTIVE, one qualifying purchase and net LKR 12,000. Preserve these facts and do not process the other 16 historical orders. These identifiers are not feature logic or new test fixtures.

## Deployment (requires separate release approval)

1. Back up the database and record current deployed commit/migration. Review pending customer recalculations and concurrent legitimate activity.
2. Run the diagnostic below before migration, selecting the actual historical order IDs from the reviewed manifest. Save sanitized output privately.
3. Apply `python -m alembic upgrade 20261125_58` in the approved migration window **before** deploying code that reads the new columns.
4. Deploy the reviewed commit. No new environment variables, Shopify scopes or consent settings are required. Keep the loyalty discount programme OFF unless separately approved.
5. Ensure the existing retail/POS worker is running through its existing deployment configuration (`RETAIL_ENABLED`, `POS_ENABLED`, `POS_LOCAL_PRINT_ENABLED` or `RETAIL_HARDWARE_LABEL_PRINT_ENABLED` already starts that worker). Do not enable unrelated hardware just for loyalty. Check pending recalculations, worker errors and membership status after a qualifying purchase.
6. Repeat the read-only diagnostic and compare selected historical hashes, membership and operational totals, accounting for genuine concurrent activity. Verify the owner baseline above remains intact and no remaining historical recovery ran.
7. Perform the acceptance checklist below before general rollout. Do not create artificial paid production orders merely for testing.

### Read-only Railway Console diagnostic

From the deployed `/app` directory:

```sh
python -m scripts.validate_customer_registration_readonly --customer-id 2
```

For a stable historical comparison, append `--historical-ids` followed by the **actual reviewed manifest's numeric order IDs**, before and after release. Omission hashes all existing order commercial fields and is unsuitable for equality comparison while real sales are changing. The report contains no names/phones, uses PostgreSQL transaction read-only plus rollback, and does not import application startup or call Shopify. `READ_ONLY_CHECK_UNAVAILABLE` is a failure, not verification. It checks database state only, not live worker health or Shopify permissions. Counts alone cannot prove absence of every unintended write.

## Reporting definitions

Registration counts use recorded UTC registration timestamps and CRM/POS sources; old unknown dates are excluded. Sources are actual registration provenance, not historical purchase dates. Current identity-conflict metric counts shared normalized-phone groups. Identified completed purchases and anonymous completed POS purchases are transaction counts, not unique anonymous people. Purchase history and loyalty qualification still use the existing canonical engine and reviewed mappings. UI shows Sri Lankan local dates; reporting periods retain the existing reporting contract.

## Staff procedure and acceptance

1. CRM inquiry: search by name, mobile, Customer ID or member ID. Candidate matches are suggestions only. Explicitly select the correct profile; otherwise register after checking for conflicts.
2. Registration success must remain visible and must not activate membership for an inquiry. Verify the profile appears in Customers and POS search. No consent is granted.
3. Create a linked CRM draft and verify Customer ID, purchaser number and independent recipient details. Alternatively choose the unidentified path and supply a meaningful reason.
4. POS: select/register a Customer, save details, and verify identity on that cart. Switch to walk-in or start the next cart and verify no prior identity remains. No loyalty discount is added at POS.
5. Through existing authorized normal financial workflows, verify a first qualifying purchase eventually creates one ACTIVE membership while discounts remain OFF. Verify refunds/cancellations recalculate, not create another membership.
6. Two staff registering the same mobile should receive one Customer plus explicit existing/conflict guidance. Multiple historical matches require review; never choose by phone alone.
7. Admin can correct contact details with a fresh fingerprint; stale edits fail. Ordinary staff cannot edit sensitive profiles, delete profiles, merge or export all customer history.
8. Repeat on desktop and a real Android browser: search, quick registration, Save draft footer, long names, international mobile, refresh, network delay, cancel/new cart. Automated Chromium mobile emulation is not physical Android acceptance.
9. Verify the original historical member/order baseline, no other historical recovery, unchanged Shopify/payment/stock/POS operations and no marketing messages.

Synthetic screenshots: [CRM desktop](images/customer-registration/crm-desktop.png), [CRM Android viewport](images/customer-registration/crm-android.png), [Customer management](images/customer-registration/customers-desktop.png), [POS](images/customer-registration/pos-desktop.png). These use disposable data, not live customer records.

## Failure handling and rollback

If registration response is lost, retry the same request key. If the user has moved to another cart, search explicitly for the registered profile; it may have been created successfully but deliberately not attached. Do not create a second profile. Resolve conflicts through administrator review without merging history.

For release rollback, stop new registration activity and redeploy the previous application version while retaining the additive schema and all Customer/member/audit/order data. Preserve durable request records and phone mutex rows. Do not reset the database, revert financial postings, delete members or replay Shopify operations. Full downgrade to `20261124_57` is only for an approved backup/maintenance procedure: it drops new metadata, registration receipts and mutex tables, not Customers, memberships or commercial history; this loses new metadata/idempotency and is not the normal rollback. Re-upgrade before re-enabling this feature.

## Validation and remaining checks

Final verification: **686 distinct tests passed; zero remaining failures, errors or skips**, including 34 new feature cases (six PostgreSQL cases and five browser cases). Runs overlap and are deduplicated by test identity. Automated results are recorded in `customer-registration-test-results.json`. Reproduce focused coverage with `python -m pytest -q tests/test_customer_registration.py tests/test_customer_registration_pos.py tests/test_customer_registration_postgres.py tests/test_customer_registration_browser.py`. Configure `DATABASE_URL` to a disposable local SQLite database and `TEST_POSTGRESQL_URL` to a local disposable PostgreSQL database named `pixiepinks_test`; PostgreSQL fixtures create/drop isolated schemas. Chromium browser tests require `/usr/bin/chromium` and the existing Playwright dependency. Never point these tests at production. The older draft-order and fulfillment PostgreSQL suites were additionally run in separate disposable databases. Tests use disposable SQLite/PostgreSQL and mocked external services plus Chromium. They include registration races, historical-tool/central-registration races, migration upgrade/repeat/downgrade, worker lock order, permissions, shared phones, first purchase/refunds, CRM/POS integration and late responses in POS and reopened CRM order forms.

No production database, Shopify connection or Railway settings were changed or inspected in this implementation. Runtime migration, production worker operation, the retained owner baseline, real Android behaviour and business acceptance require the separately authorized operator checks above. No merge or deployment is performed by this PR.
