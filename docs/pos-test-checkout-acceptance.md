# Isolated POS TEST checkout → automatic 80 mm receipt acceptance

## Scope and defaults

This is a separate administrator workflow, not a new way to record a customer sale.
It creates an immutable synthetic checkout in `pos_receipt_tests`, then atomically
finalizes that test and queues one `TEST` hardware job. It NEVER calls Shopify
CREATE/read-back, creates a POSCheckout/POSOperation/SalesOrder/SalesPayment, opens a
cash session, adjusts inventory, posts accounting or changes a customer balance.
Synthetic LKR 350.00, discount, tax and cash/change fields are fixture data, not tenders.
The historical `POS-851C4716B015DF1B0E79` REVIEW operation remains untouched and
continues to fence real Shopify work. This test does not establish live Shopify or
normal finalized-sales readiness.

`POS_TEST_CHECKOUT_ENABLED=false` by default. Deployment does not enable it. Existing
production automatic receipt policy is not changed; keep `auto_receipts=false` on
both the selected original device and other devices during acceptance. No Shopify
write flag is required. No new Windows binary, credential rotation, key or app needed:
PR #192 already accepts TEST jobs, encrypted envelopes and nullable spool IDs.

All create/read/finalize test endpoints require existing authenticated POS admin
permission (ADMIN or explicitly granted pos.admin with pos.view), active user,
POS_ENABLED, plus the new feature gate. POSTs retain existing CSRF/body limits.
Physical review of these jobs also requires admin, including after the feature is off.
No browser query parameter can enable the feature. Test APIs do not use the POS
operation dispatcher. This independence does NOT bypass a printer's UNKNOWN fence.

## Data and lifecycle

- Create freezes a fixed synthetic fixture, selected device/version, actor and SHA-256.
  A unique `(shop, request_key)` prevents repeated create requests from duplicating it.
- Finalize Test is a separate explicit confirmation. The test UUID is its permanent
  idempotency identity. Under the existing shop/device locks it renders the canonical
  true80mm Unicode receipt with `TEST — NOT A VALID SALE`, `NO PAYMENT RECEIVED`,
  `NOT SHOPIFY VERIFIED` and a `TEST-<UUID>` reference. Normal receipts are unchanged.
- One unique hardware business key `receipt:test-checkout:<UUID>` is inserted with
  kind TEST, no checkout/session/label/parent financial linkage and `test_only=true`.
  Final status FINALIZED, job link and TEST_CHECKOUT_FINALIZED/TEST_RECEIPT_QUEUED
  events commit in the SAME transaction. Rendering/commit failure cannot leave a
  half-finalized test. A retry always returns the same job, even if FAILED/UNKNOWN,
  expired or the device later changes; it never silently generates a replacement.
- Worker/agent uses normal actor authorization, source/hash validation, device version,
  media, ready queue, single-use dispatch permit, encrypted transport and journal.
  Test gates are checked again before dispatch. A test cannot use barcode, A4 or drawer
  controls. No change to Windows GDI, replay fences or acknowledgements.
- Test checkout status DRAFT → FINALIZED is distinct from printer status QUEUED →
  DELIVERED_TO_AGENT → SUBMITTED_TO_PRINTER/UNKNOWN/FAILED. Submission is audited by
  AGENT_SUBMITTED_TO_PRINTER; physical_output_verified remains false. Only accurate
  explicit operator review yields CONFIRMED/STAFF_REVIEWED with physical flag true.
- Expiry (24h), seven-day PDF retention, pauses, mismatches and UNKNOWN handling remain
  unchanged. No automatic retry or reprint exists for a finalized test. Prior attempts
  in SQLite never dispatch again on restart. New tests are separate explicit authorizations.
- Browser persists a pending create request key in localStorage; refresh/lost response
  reuses it. Finalize retries use the test ID. “Prepare another separate test” is an
  explicit new-identity action, NOT recovery for ambiguous printing. Recent50 tests
  are listed; exact IDs remain retrievable by their individual GET endpoint.

## Review and deployment (NOT performed automatically)

1. Review/merge PR separately. Back up normally; deploy only with release approval.
2. Apply additive migration `20261122_50` after `20261121_49` using the normal release
   procedure (`alembic upgrade head` in the approved runtime). It creates only the new
   test table/unique indexes and immutable-identity triggers; existing financial,
   hardware/history/idempotency rows are not rewritten. Downgrade intentionally retains
   evidence. Do not migrate by importing application startup or manually editing rows.
3. With new feature false, verify existing POS, WhatsApp, manual receipts, migration
   and encrypted connector operation unchanged. Cash drawer and barcode hardware gates
   stay false; isolated ingress stays false. No production Shopify flags/scopes change.
4. Obtain separate owner approval to enable ONLY `POS_TEST_CHECKOUT_ENABLED=true` in
   the selected test/runtime service. Existing required gates are POS_ENABLED,
   POS_LOCAL_PRINT_ENABLED and PRINT_CONNECTOR_ENCRYPTED_ENABLED; keep them at approved
   values. A redeploy/restart may be needed to load a changed environment flag. No
   production configuration is changed by this PR or automatically by its UI.
5. Confirm selected active receipt-only device UUID/register/location; dedicated80,
   POSPrinter POS-80/USB001, no drawer queue/verification/auto_cash_movements. Production
   auto_receipts remains false. A hardware UNKNOWN/DELIVERED/QUEUED on THIS device
   blocks Finalize Test. Never clear it for convenience. Use separately approved
   recovery or a clean independently authorized device, not a bypass.

## Exact admin UI and physical acceptance checklist

Obtain approval for one synthetic dispatch and printer ownership before these steps.
The existing original device is `f18b74ce-cf1f-440f-973f-579640ff46e6` (Register1);
verify live state, do not assume previous test outcome permanently establishes readiness.

1. Stop competing acceptance/production connector processes safely when idle; ensure
   no scheduled process can compete. Do not change drivers, barcode queue or journals.
   Use original Windows account with its preserved DPAPI credentials/journal and the
   installed reviewed PR192 connector. Never copy another account's profile or reset it.
2. Read-only capture original POS REVIEW operation, protected print-job states/audits,
   finance/payment/posting counts and all printing policies. Confirm selected hardware
   queue empty, unpaused, receipt roll installed, media verified, local attestation
   unchanged and encryption configured. No policy save/activation/rotation is needed
   for an already healthy connector. Resolve any mismatch through separate approval.
3. After authorized startup ONLY, use the original installed Python/environment:

   ```powershell
   Set-Location C:\PixiePinks\ReceiptConnector-190
   & .\.venv\Scripts\python.exe -B -m windows_print_agent status
   & .\.venv\Scripts\python.exe -B -m windows_print_agent run
   ```

   Stop if the status UUID/account does not match. Never run two agents concurrently.
4. CRM Admin → Point of Sale → amber **TEST checkout · not a sale**. No register opening,
   cash float, normal cart, payment collection or real Checkout action is needed.
5. Select exact dedicated connector. Click **Create Test Checkout** once. Record TEST
   reference. Page refresh or Create again recovers the same request, not a new sale.
6. Click **Finalize Test**, confirm the explicitly non-financial dispatch. Do not click
   the old financial sale's Finalize/Reconcile or enable automatic finalized receipts.
7. Observe **Automatic Receipt Status** for exactly one new job. Require one physically
   readable/aligned receipt with TEST marking, synthetic400 subtotal/50 discount/350
   total/500 cash/150 change and test tax0; drawer stays closed. Never treat these as
   money received. Normal printer roll/driver settings remain unchanged.
8. Require server SUBMITTED_TO_PRINTER, attempts1, exactly one AGENT_SUBMITTED_TO_PRINTER,
   local SUBMITTED_TO_PRINTER/spool_id=null allowed/acked1. At this stage physical flag
   must still be false. No TypeError/driver_submission errors or extra jobs/paper.
9. Stop/restart the SAME connector safely while idle with SAME profile/journal; no new
   test, pairing or key. Observe several polls (≥20sec) with zero duplicate output.
10. CRM Hardware → locate that EXACT test job ID → enter honest physical-observation
    notes → **Confirm physically printed / drawer observed** only after observing it.
    Test view then shows operator confirmation; audit STAFF_REVIEWED persists.
11. Compare baseline: original financial REVIEW, historical print jobs, real orders,
    tenders, inventory and balances unchanged; selected policies/auto_receipts unchanged;
    no new POS operation, SalesOrder, SalesPayment, journal or cash movement. Normal
    background expiry/retention may occur independently; distinguish it with timestamps.
12. Stop idle test connector if required by maintenance approval. Keep production
    auto_receipts and cash drawer OFF. This test is not permission for live sales cutover.

## Stop conditions, recovery and rollback

- If output/ACK is uncertain, STOP. Do not repeat Create with a new key, prepare another
  test, replay, purge spooler, delete SQLite, reset attempts, force-confirm or restore an
  old journal backup. Preserve job ID, sanitized stages and original audit. UNKNOWN is
  intentionally durable. Repeat finalize/get is safe and returns the SAME identity;
  it is not a physical-print retry.
- FAILED/expired tests do not regenerate. A different test requires a separate honest
  review and explicit new authorization. No automated financial recovery is involved.
- Disable new feature under separately approved release/configuration procedure, and
  pause the selected device/stop idle connector as needed. Gate off stops new finalizes
  and future test claims/permits but cannot recall an already consumed permit/in-flight
  paper. It does not stop ordinary workflows controlled by their existing gates.
- Preserve table, test identity, hardware jobs/audit and latest journal. No destructive
  downgrade. Before reverting code, pause/stop and ensure no outstanding new test jobs:
  OLD code lacks this new feature's gate/source check, although its journal fence still
  applies. Do not roll old code over queued tests or re-enable devices blindly.
- Do not change Railway keys, Shopify variables/scopes, financial records or original
  REVIEW state as a rollback. Default-off feature rollback does not require touching them.

## Security/operational boundaries

A separate test backend is still preferable for unlimited development. On an existing
runtime this path isolates domain writes, not unrelated background activity: existing
workers continue their independently authorized jobs. It preserves locks and policies,
not a false claim that no other operator/background process can change production.
Exactly one durable job/dispatch attempt is enforced; driver/firmware physical output
still requires eyewitness acceptance. Successful spooling is never physical proof.

## Automated verification for this change

Final results on the PR branch:

- Broad mocked POS/Shopify adapter, normal/provisional receipt, encrypted transport,
  Windows GDI/journal/diagnostics, browser and WhatsApp regressions: 244 passed,
  1 initially skipped (PostgreSQL finance fixture not configured in that run).
- Final dedicated synthetic checkout suite after all assertions/UI updates: 17 passed.
  Fifteen overlap the broad suite; two additional isolation/rollback cases are new.
- Disposable localhost PostgreSQL suites: 17 passed, including concurrent test
  create/finalize, migration/immutability, legacy additive upgrade, hardware replay,
  POS incident/provisional and the previously skipped finance aggregation test.
- Unique cases covered across runs: 263 passed. No remaining failed or uncovered
  skipped case from these runs. Existing deprecation warnings remain.
- Python compilation, JavaScript syntax and git whitespace checks passed. Actual
  physical printing is deliberately NOT performed by development/test execution.

Reproduce with the repository test environment, mocked Shopify/Windows fixtures and
an explicitly disposable `TEST_POSTGRESQL_URL` (localhost database `pixiepinks_test`):

```bash
python -m pytest -q tests/test_pos_receipt_tests.py
python -m pytest -q tests/test_pos_receipt_tests_postgres.py tests/test_receipt_connector_postgres.py tests/test_hardware_postgres.py tests/test_pos_postgres.py tests/test_pos_incident_postgres.py tests/test_pos_provisional_postgres.py tests/test_retail_postgres.py tests/test_finance_reporting_pos.py::test_postgres_financial_aggregation_uses_decimal_and_inclusive_dates
```

Do not point test fixtures at production. Test fixture migration reconstruction now
removes the new dependent test table before older hardware parents, matching its FK
order; that test-only teardown never runs during an application migration.
