# Phase22A — optional protected customer-field read-back

## Verified cause and change

Production evidence supplied by the owner identifies ACCESS_DENIED at
`order.customer` in the mandatory full GraphQL read; identity, totals, lines,
transactions and fulfillment isolate successfully. Main inspected for this change:
`2a44a0a17987f35da4dbdf73fb035487f7c6fe36` (includes Phase21/22 controls).
No fresh authenticated production request or record update was performed here.

`pos_shopify.ORDER_FIELDS` previously unconditionally selected `customer { id }`.
No names, emails, phones or addresses were requested there. The shared read is used
by financial completion, no-resend reconciliation and refund evidence. Optional
protected access therefore prevented financial verification even for an unbound
walk-in checkout. Receipt customer/cashier display data comes from the original
frozen CRM snapshot; it does not need a new Shopify customer read.

The canonical `READ` now selects the same financial/order/fulfillment/refund fields
without customer. `ORDER_CUSTOMER` is a separate same-order query for `id customer
{ id }` using the existing authenticated client, API2026-10 and shop/currency checks.
No global API/webhook version, permission, scope, token management, import, Draft
Order, financial schema, frozen snapshot or idempotency contract changed.

## Required vs optional evidence

- If `frozen.customer_shopify_id` is None/absent, no Shopify customer association was
  explicitly requested by that checkout. Customer data is OPTIONAL and not queried
  for completion/reconciliation. Missing evidence is omitted, never fabricated as
  null or a fake customer. This does not assert that the remote order has no customer.
- If an actual response supplies an unexpected non-null association for an unbound
  checkout, verification still rejects it as before. It is not silently discarded.
- If the frozen checkout contains an explicit customer GID, association is REQUIRED.
  The service obtains the separate association response when absent from the
  financial read. Its order ID must equal the requested ID, and its customer ID must
  equal the exact frozen GID. Explicit null, missing, mismatched or denied association
  stays fail-closed; no financial posting/COMPLETE/receipt occurs.
- `CUSTOMER_ASSOCIATION_UNAVAILABLE` is an allowlisted diagnostic code for unavailable
  required evidence. Actual permission/access failures must be inspected via the
  separate customer diagnostic; the code can also represent transport unavailability.
  The existing app's protected-customer-data approval/necessary read access needs
  separate administrator review for genuinely linked customers. This PR does not
  request or grant scopes, suggest inventing customer data, or treat a separate
  customer profile lookup as proof of its association to this order.
- GraphQL errors and partial responses remain rejected. No error is filtered out to
  salvage a partial financial payload. Complete financial verification still requires
  exact order/reference/tags, payment/currency/received/refunded amounts, tax/discount,
  complete lines and fulfillment quantities/location/status. Actual read-back order
  ID must match the requested/recorded identity; reconciliation cannot substitute a
  different search result for a known ID. No CREATE retry behavior was added.

## Diagnostics

The existing `scripts.diagnose_pos_order_readonly` full group now means the complete
canonical required financial selection. It reports:

- `financial_query_includes_customer=false`;
- `customer_association_required` according to the actual frozen local checkout;
- `verification_role=REQUIRED_FINANCIAL_FIELDS` for full;
- `OPTIONAL_PROTECTED_CUSTOMER` or `REQUIRED_ASSOCIATION` for customer;
- required linked association read metadata when it must be fetched for verification.

The full group verifies linked associations through the separate query automatically,
so a financial READ_SUCCEEDED by itself cannot incorrectly verify a linked checkout.
Supplemental results are cached within the bounded run and throttle responses stop
further probing. Raw GraphQL messages, customer values, credentials and exception
payloads remain redacted. `scripts.inspect_pos_sales_readonly` uses the same service
read policy and allowlists only canonical read/search plus association queries.
The Phase22 combined read-only diagnostic inherits these changes.

## Migration and release

**No database migration.** Head remains `20261122_51`. No new flag or approval table.
All production flags, Phase21 TEST mode, Phase22 pilot scope/cart guards, normal
receipt rendering, hardware history/journal and encrypted transport remain unchanged.

1. Review/merge/deploy only under separate release approval; this task does none.
2. Keep `POS_REAL_SALE_PILOT_ENABLED=false`, general automatic receipts and cash drawer
   OFF; do not alter Shopify scopes or Railway variables to deploy this read fix.
3. Reload the existing deployment through the normal approved release process and
   verify its commit/schema state. No Windows agent update is required.
4. Run the following READ-ONLY command inside the authorized Railway CRM Console:

   ```bash
   cd /app && python -B -m scripts.diagnose_pos_order_readonly --order-id gid://shopify/Order/7610874233135 --groups full,customer
   ```

5. For an unbound historical checkout expect `customer_association_required=false`,
   full READ_SUCCEEDED and `canonical_verification=VERIFIED_AGAINST_FROZEN_SALE_A`.
   Optional customer GRAPHQL_ERROR/ACCESS_DENIED at order.customer may remain and
   does not invalidate the required financial verification. If the checkout is
   actually linked, customer denial MUST produce NOT_VERIFIED; do not change the
   frozen binding to make it pass. Any other required failure remains NO-GO.
6. Confirm local posting/queue conditions without writing:

   ```bash
   cd /app && python -B -m scripts.inspect_pos_sales_readonly --reference POS-851C4716B015DF1B0E79 --reference POS-5AB126C311C38CDBD5FA
   ```

7. Preserve REVIEW and all queued operations during diagnosis. A verified diagnostic
   is NOT reconciliation approval. The reported THREE queued CREATEs must be reviewed
   individually for attempts, tenders, stock, identity and prior remote outcomes
   BEFORE any separately approved recovery that could release them. Reconciliation
   performs local financial posting and can release normal worker processing. Do
   not click it, start another sale, resend CREATE or force COMPLETE under this PR.

No claim is made that the production order now passes the changed full selection;
that requires the post-deployment read-only command above. If refunds/other required
fields reveal an additional access/contract problem, stop and investigate it rather
than ignoring another GraphQL error.

## Rollback and limitations

Code-only rollback under separate approval restores the previous shared selection,
which will likely restore the protected-field blockage for walk-ins. It does not
undo any financial recovery; no rollback of journals/orders, deletion, attempt reset
or replay is permitted. Preserve any completed accounting and hardware identities.
Do not roll back to evade required customer checks or enable pilot/print flags.

This fix separates optional data from financial evidence; it does not resolve
customer-linked permission approvals, automatically reconcile historical data,
release the Shopify queue or authorize a real-sale pilot. Refund reads retain all
existing required refund fields; unrelated imports/Draft Orders are not modified.

## Automated verification

- New protected-field transport/association cases plus adapter/diagnostic focused run:
  54 passed (overlaps the broader run).
- Broad protected-field, canonical POS, preserved Phase21 TEST/Phase22 pilot,
  receipt/encrypted connector, exact-order/no-resend diagnostics, historical cleanup,
  financial and WhatsApp/browser regressions: **250 passed, 0 failed**, one initially
  skipped PostgreSQL finance case.
- Disposable localhost PostgreSQL customer-free walk-in read-back/no-resend posting,
  three-queued-operation boundary, pilot/test/hardware concurrency, existing financial
  recovery and finance suites: **17 passed, 0 failed**, including the initially skipped
  finance test. No unresolved skipped or failed cases in these runs; 267 unique cases
  completed across broad/PG runs. Existing deprecation warnings remain.
- Python compilation and whitespace checks passed. No live database or Shopify mutation,
  reconciliation, permission change, pilot activation or physical printing was performed.

Tests are disposable/mocked. Never run fixture migrations or mutation tests against
production. Suggested reproduction in the test environment:

```bash
python -m pytest -q tests/test_pos_customer_readback.py tests/test_pos_adapter.py tests/test_pos_read_transport_diagnostic.py
python -m pytest -q tests/test_pos_customer_readback_postgres.py tests/test_pos_real_sale_pilot_postgres.py tests/test_pos_receipt_tests_postgres.py tests/test_receipt_connector_postgres.py tests/test_hardware_postgres.py tests/test_pos_postgres.py tests/test_pos_incident_postgres.py tests/test_finance_reporting_pos.py::test_postgres_financial_aggregation_uses_decimal_and_inclusive_dates
```

Pilot OFF does not stop ordinary untagged queued CREATE operations. Before any later
reconciliation, obtain explicit approval for queue-release effects and a reviewed
worker-maintenance/recovery plan for all three queued transactions. This read fix
provides no such write approval.
