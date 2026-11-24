# Customer and delivery recipient separation

Baseline: main `203cff8` (PR #208). Branch: `feature/order-customer-recipient-separation`. Migration: `20261124_57`, parent `20261124_56`.

## Root cause

`shopify_draft_order_service._validate_local_order` validated the purchaser's WhatsApp as a Sri Lankan mobile, and `_shopify_input` reused it as both the draft phone and shipping phone. Overseas gift buyers were therefore rejected before a draft could be sent. The old data model had a recipient name but no independent recipient mobile.

Purchaser identity now stays in `customer_id`, `customer_name` and `whatsapp_phone`; the original conversation association is preserved. Country is derived from the existing international-phone infrastructure and is a telephone country, not a verified residence. Recipient data never creates, merges, changes or becomes a loyalty Customer.

## Data and validation

Existing `delivery_contact_person` is the order-specific recipient name. Additive columns are `delivery_phone`, `delivery_alternative_phone`, `delivery_country` (LK default), `delivery_notes` and `delivery_method` (DELIVERY default; PICKUP/IN_STORE explicit). Existing `alternative_phone` remains unchanged. No Customer/profile, accounting, inventory, payment, Shopify identifier or loyalty table is rewritten.

Customer validation uses the existing international E.164 normalization infrastructure, retaining the existing digits-only CRM identity convention. Formats normalize without forcing +94. `phonenumbers` checks plausible international numbers; this does not verify ownership or carrier reachability. Local phones accept formatted `07…`, `947…`, `+947…` and normalize to `+947XXXXXXXX`. Alternative phones are checked only when provided. Errors identify the local primary/alternative field without printing its value.

Local drafts may omit delivery details. DELIVERY requires a recipient name, valid primary mobile, address, city and district before Shopify draft creation. Optional postal code remains optional. Explicit PICKUP/IN_STORE omit shipping address and do not require delivery contacts; Shopify-returned tax and total checks remain authoritative. An API price/tax mismatch is still a review condition, not permission to change financial totals.

## Interfaces

- WhatsApp Create Order/Edit: Customer Information with original name/WhatsApp and derived country; separate Local Delivery Information for recipient and contacts, method, notes and existing address fields.
- Same-customer checkbox copies name and only a valid local mobile. Overseas phones are not copied into recipient primary. Existing manual values require confirmation before replacement. Fields remain independently editable; manually editing recipient name/phone clears the convenience checkbox.
- Sales Orders → Create sales order (`/crm/order-entry`) adds manual entry using the same authenticated draft API and current Shopify product resolver. It searches exact variants, accepts quantities and optional explicit Customer ID. Main order validation/loyalty identity checks still govern that association. It does not manufacture a WhatsApp conversation. Manual entry creates only a local draft and preserves a stable idempotency key across retries.
- Detail page separates purchaser and recipient, shows missing local contact, and exposes the existing authorized Edit delivery details dialog. It can now correct recipient/name/phones/address/notes/method as well as city/district/postal code.
- Delivery edits retain existing CSRF, conversation visibility/ownership, edit permission, row lock, optimistic version and dispatch/Shopify-link locks. Unknown fields (including purchaser identity, money and status) are rejected. Audits capture actor/time and changed before/after delivery values, not unchanged profile data. Draft-editor changes also produce recipient audit history.
- Country lookup uses authenticated CSRF-protected POST JSON; phone values are not put in request URLs. No customer API credentials or phone values are logged by new code.

Screenshots use synthetic buyers/recipients:

- [WhatsApp desktop](images/order-recipient/whatsapp-desktop.png)
- [WhatsApp mobile](images/order-recipient/whatsapp-mobile.png)
- [Manual mobile](images/order-recipient/manual-mobile.png)

## Shopify mapping

Official reference reviewed successfully (HTTP 200):

https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/DraftOrderInput

https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/PurchasingEntityInput

The documented `phone`, `shippingAddress`, `customAttributes` and `purchasingEntity` fields are used. No global API/webhook version, credentials, scopes or live settings change is introduced. Actual production negotiated-schema/permission behavior was not accessed here.

| Input | Source |
|---|---|
| `phone` | Purchaser's normalized international WhatsApp |
| `purchasingEntity.customerId` | Only an existing shop-scoped `POSCustomerBinding` for the authoritative purchaser Customer ID; that existing binding workflow verifies the remote customer. No name/phone lookup or new binding is invented. |
| `shippingAddress.firstName` | Order's local recipient name |
| `shippingAddress.phone` | Independent normalized local primary mobile |
| `address1`, `city`, `province`, `zip`, `countryCode` | Order's local address/city/district/postal code, LK |
| Custom attribute `Local alternative phone` | Local alternative contact, when supplied |
| Custom attribute `Delivery instructions` | Order delivery notes, when supplied |

When no existing verified purchaser binding exists, none is fabricated from the recipient, a name match or a loyalty-only association. Existing draft tags, idempotency keys, sync claims, duplicate searches, lifecycle and tax/total read-back safeguards remain in force. A draft is not completed, paid, reserved, fulfilled or posted merely by recipient editing or draft creation.

## Existing orders and safe recovery

Migration adds only recipient columns. It copies a valid legacy local purchaser phone **only when an existing recipient name matches that purchaser name**. It copies a valid legacy alternative in that case without overwriting an existing dedicated alternative. It does not infer names, copy overseas phones, infer that a different recipient owns the purchaser's phone, trigger a sync or create a Customer. Overseas or ambiguous legacy contacts remain blank and require staff completion.

For a failed local-validation attempt:

1. Open the existing SalesOrder, check its existing Shopify identifiers and synchronization status.
2. If there is no linked Shopify order/draft or uncertain sync, use Edit delivery details under existing permissions. Enter the actual local recipient name/primary and address; save. Purchaser identity and money remain unchanged.
3. Retry the existing Shopify draft synchronization action, using the same SalesOrder. Do not recreate the sales order or reset sync attempts/keys. Tests verify corrected local validation followed by repeated synchronization creates one draft.
4. If a remote draft/order is already linked or synchronization is SYNCING/RECONCILIATION_REQUIRED, the existing edit locks remain. Use the established read-only reconciliation procedure. This feature does not authorize resending an uncertain write or modifying a linked Shopify address.

Keep existing production POS REVIEW transactions untouched. No loyalty activation/reconciliation gate is enabled or bypassed.

## Documents and privacy

POS/customer sales receipts retain the purchaser information and existing receipt rendering/printing infrastructure. New fulfillment delivery snapshots use recipient name and local address/contact/notes, with no fallback to purchaser name and no overseas phone added for delivery personnel. Existing fulfillment snapshots are historical and are **not automatically rewritten** by this migration. Review/reissue pre-existing delivery documents through the existing authorized fulfillment workflow where necessary. This change does not implement a new courier API, shipping-label renderer or print-agent dependency.

## Deployment

Separate owner release approval is required. No production data or live Shopify mutation was performed during implementation.

1. Back up the production database and verify revision `20261124_56` and the expected reviewed commit. Preserve existing flags and credentials.
2. Apply `alembic upgrade 20261124_57` using the established release procedure before running matching application code. Review conservative backfill on a staging copy first.
3. Deploy reviewed code. No new dependency or environment variable is required; the existing phonenumbers library is reused.
4. Verify authenticated desktop/Android form access, actual derived customer country, manual draft save, recipient-only editing, audit and missing-contact warnings. Use staging/test data first; no automatic Shopify send is needed for smoke testing.
5. With separate approval for an actual draft operation, verify the installed Shopify API/scopes accept documented fields, order/contact mapping, metadata and read-back totals. Review a Kuwait/UAE gift buyer and a local recipient. Do not complete orders or alter production finances to test this release.

## Rollback

Prefer a forward correction. Before any code rollback, administratively stop Shopify draft synchronization and staff sync actions: the old adapter ignores dedicated recipient fields and reuses the purchaser phone, so it is unsafe for new gift orders. Do not resume draft synchronization under old code without explicit compatibility review. Retain additive recipient columns and audit history. Export recipient data before any schema downgrade. `alembic downgrade 20261124_56` drops the new recipient columns and destroys those new values; it does not change the old purchaser columns, Shopify IDs, accounting or stock. Do not downgrade while new code/workers still expect the columns. No Shopify address/order reversal, financial rollback or sync-key reset is part of this procedure.

## Acceptance and remaining verification

- Overseas Kuwait/UAE/other E.164 buyer + local recipient; local self/different recipient; multiple orders/recipients without purchaser or loyalty reassignment.
- Blank/invalid local primary blocks DELIVERY synchronization, optional alternative validates only when supplied, blank drafts save, pickup/in-store omit delivery.
- Buyer phone remains unchanged when recipient edits occur; same-customer overwrite requires confirmation; countries are derived from the buyer phone.
- Correct missing delivery contact on an eligible existing failed draft and retry once under normal authorization; repeated requests retain one draft.
- Verify existing verified purchaser binding and absence of recipient-based Customer creation.
- Restricted staff, stale edit versions, dispatched/linked orders, CSRF and attempted identity/amount/status injection remain blocked.
- Customer receipt retains buyer; newly generated delivery snapshot contains recipient/local phone, not the overseas buyer contact. Existing physical print flow remains unchanged.
- Test true Android and physical document output separately; browser-width tests do not establish physical-device acceptance.

Production API compatibility, real overseas orders, live draft permissions, real Android and physical document acceptance remain unverified. These require operator review and separate authorization. No live settings, migrations, customer records, Shopify writes or deployment were executed here. See the test-results manifest for retrieved run counts and any unresolved items.

## Retrieved automated results

**459 distinct tests passed, including 32 new recipient cases.** Runs overlap: 225 recipient/CRM/loyalty cases, 166 POS/Inbox/finance/purchasing/inventory cases, 53 PostgreSQL draft cases, 18 PostgreSQL fulfillment cases, 48 receipt PDF cases and 5 final browser/identity cases. The one SQLite-only draft concurrency skip subsequently passed in PostgreSQL; no unresolved failures/skips remain. [Per-run counts](order-recipient-test-results.json).

The final API tests cover multiple buyers sharing a recipient, multiple recipients for one buyer, no recipient Customer creation or loyalty transfer, forbidden customer-ID injection, audited multiline delivery notes, country lookup, manual/WhatsApp entry and safe correction/retry. PostgreSQL tests reconstruct the old schema and verify conservative backfill and rollback without changing purchaser numbers or totals. Existing framework/deprecation warnings remain. Python compilation, JavaScript syntax checks, `alembic heads` (`20261124_57`) and `git diff --check` passed.

Example local test groups (disposable databases only):

```sh
python -m pytest -q tests/test_order_recipient.py tests/test_order_recipient_identity.py tests/test_order_recipient_browser.py tests/test_order_recipient_postgres.py
python -m pytest -q tests/test_shopify_draft_order_sync.py tests/test_sales_order_delivery_location.py tests/test_sales_order_delivery_location_ui.py tests/test_loyalty.py
```

Set `TEST_POSTGRESQL_URL` to a localhost disposable database named `pixiepinks_test` for the existing PostgreSQL fixture. Run the Shopify draft and fulfillment suites with separate disposable global PostgreSQL `DATABASE_URL` values; their legacy cleanup fixtures do not clean one another's foreign-key dependencies. Use `PGCLIENTENCODING=UTF8` where necessary. Browser tests require the existing Playwright/Chromium installation. Never run these mutation tests against production.
