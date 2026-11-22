# Phase 22 — real POS sale / Shopify / automatic 80 mm receipt pilot

## Release assessment

Code baseline: main `bd9bdc878eab8fb8b30f98f9897018f81372a97e` (merged PR193),
including physically accepted PR192 Windows behavior. Phase21 synthetic TEST checkout
is unchanged. This is a real financial pilot, NOT a synthetic endpoint or queue bypass.

Live release is **NO-GO until fresh production evidence and separate approvals**.
The reported historical `POS-851C4716B015DF1B0E79` REVIEW remains a shop-wide blocker.
No live Railway credentials/runtime were accessible during implementation; live
commit, migration, scopes/order read-back, balances and device state are NOT freshly
verified here. Schema-level checkout-ready does not prove actual order readability.
No root cause for the historical full-read failure is invented from old evidence.

## Controls and data

- `POS_REAL_SALE_PILOT_ENABLED=false` by default. Existing checkout/refund/product/
  inventory flags are untouched. `auto_receipts` stays false on all production
  devices. Cash-drawer automation must remain false, no drawer queue is permitted.
- Additive migration `20261122_51` follows `20261122_50`; creates only immutable
  `pos_pilot_scopes` and `pos_pilot_authorizations`. Scope binds current configured
  shop + exact register/location/device. Only existing POS admin can explicitly
  configure it. It is immutable: changing target needs a separately reviewed release,
  not an unlogged edit. SQL update/delete triggers retain approval evidence.
- Before taking payment an admin authorizes ONE specific DRAFT cart, current payload/
  quote/version hash, device/version, notes and stable request key. Approval expires
  after30min before checkout; paid/attempted sales keep their recovery identities.
  Cart edits/version changes invalidate that approval; no silent refresh of approval.
  Only an unpaid DRAFT can be replaced by a new explicitly approved cart. Never
  create another cart/payment to replace an existing paid/queued/uncertain sale.
- Scope/authorization refuse any current-shop queued, dispatching, uncertain, REVIEW
  or CONFLICT financial operation, and any other incomplete checkout. They also
  require a connected/attested, empty/unpaused dedicated80 device and honestly
  CONFIRMED current-version TEST. No financial ordering domain is skipped. A separate
  deployment/shop/database is required if the current domain cannot safely progress.
- Normal real checkout still verifies tender, stock/price/tax, accounts, session,
  frozen snapshot and Shopify schema/scopes. It alone creates sale/payment records.
  Pilot authorization does not accept money, create orders or authorize other carts.
  A subsequent normal checkout of the bound cart revalidates approval; no synthetic
  TEST data or TEST endpoint can produce a real order.

## Financial state machine and receipt transaction

Stored status names remain compatible with existing PostgreSQL triggers and queues.
Staff see a pilot projection, not replacement SQL statuses:

| UI phase | Existing source |
|---|---|
| DRAFT | Editable cart, before real checkout |
| PAYMENT_PENDING | AWAITING_PAYMENT (bank transfer) |
| PAYMENT_VERIFIED | PAYMENT_READY after bank verification |
| SHOPIFY_CREATE_PENDING | QUEUED / dispatching operation with durable attempt fence |
| SHOPIFY_READBACK_PENDING | REVIEW with known remote ID and read/verification failure |
| ACCOUNTING_PENDING | REVIEW with LOCAL_POSTING/LOCAL_FLUSH diagnostic |
| COMPLETE | Verified Shopify + committed local posting |
| REVIEW / FAILED | UNCERTAIN, review/conflict or cancelled; explicit action required |

Cash/card verification flows directly to queued CREATE at normal checkout. Bank
requires existing administrator bank verification and cashier Finalize. Repeated
checkout keys and repeated pilot finalization return the existing sale; they do not
capture another tender. The existing shop-wide REVIEW/UNCERTAIN/DISPATCHING fence
runs BEFORE selecting any CREATE, including pilot work.

Order CREATE is at most one mutation attempt with durable reference/tags/intent.
A lost response stays UNCERTAIN; no automatic CREATE retry. Existing admin **Verify
Shopify & finalize locally (no resend)** finds exactly one correlated order, performs
full frozen identity/payment/line/fulfillment/location verification and reuses it.
Zero/ambiguous/mismatched results never authorize another CREATE or force COMPLETE.
Read-back must also return the exact requested order ID, and a recorded remote ID
cannot be silently substituted by a different search match during reconciliation.
The button mutates local accounting if verification passes; it is NOT a read-only
financial operation, and releasing a blocker may dispatch other queued work.

`publish_sale` posts the canonical balanced/idempotent journal and links the order/
payments. In the SAME completion transaction, an authorized pilot reserves exactly
one `receipt:auto:<checkout UUID>` job using the existing canonical renderer. It
includes the verified Shopify order reference and actual frozen line/variant,
quantity/unit/discount/tax/tender/change values; no TEST watermark. Printer/render
failure instead reserves one durable FAILED/PILOT_RECEIPT_REQUIRES_REVIEW identity:
verified finance commits, and no render/print loop or repeat Shopify write occurs.
If the database cannot commit the posting+receipt reservation atomically, the existing
operation remains REVIEW with known order/attempt; explicit read-back recovery is
required. Never interpret a lost DB commit response as permission for a new sale.

General auto-print worker excludes pilot carts, so enabling general auto policy
cannot accidentally generate a second original or bypass pilot release gating.
The queued pilot job is rechecked against its approval and verified operation/
posting/order identity. Existing encrypted claim/permit, source/version, pause,
UNKNOWN, readiness, journal/fsync, null spool-ID and no-replay rules remain.
Printer disconnection/pause after payment does not reverse a verified sale: the
receipt stays queued/fenced. Submission is not physical proof. Staff see financial
and receipt status separately; only honest explicit review sets physical confirmation.

## Read-only production diagnosis — operator execution only

After separately approved deployment/migration, in the existing Railway CRM Console:

```bash
cd /app && python -B -m scripts.diagnose_pos_pilot_readonly
```

Sanitized JSON includes runtime commit/source hashes, migration, effective flags,
financial blockers, pending counts, selected scope/devices and existing historical
sale/order read-back and transport field diagnostics. No key, token, customer receipt
or raw exception is printed. PostgreSQL transactions are read-only; no app startup,
worker, reconciliation, CREATE/refund or inventory mutation is invoked. Successful
DIAGNOSTIC_COMPLETE is report completion, not proof that nested Shopify reads passed
or permission to release financial work.

The existing diagnostics can run BEFORE Phase22 deployment:

```bash
cd /app && python -B -m scripts.inspect_pos_sales_readonly --reference POS-851C4716B015DF1B0E79 --reference POS-5AB126C311C38CDBD5FA
cd /app && python -B -m scripts.diagnose_pos_order_readonly --order-id gid://shopify/Order/7610874233135 --groups full,identity,totals,customer,lines,transactions,fulfillment
```

Check allowed group names against `FIELDS` in the deployed script; use `--groups full`
if the release differs. Do not infer safe full verification from partial field success.
Verify Railway dashboard commit/migration, actual remote ID and exact correlation,
paid LKR totals/tax, complete line/quantity mapping, payment transaction evidence,
correct location fulfillment, frozen hash and local posting/account links.

### Historical recovery choices — NOT executed by this feature

1. Actual order full-read now verifies: review existing local posting/tenders and
   obtain explicit accounting/reconciliation approval plus any queue-release effects.
   Use the existing no-resend reconciliation UI. Keep its idempotent posting and
   exact-one-match conditions; never force COMPLETE or reset attempts. Recovery uses
   the existing current Sri Lanka posting-date policy; review accounting-period and
   register/payment dates before approval, without backdating or rewriting history.
2. Full-read fails: use sanitized field/transport diagnostic to identify the exact
   API/scopes/version/selection/read-back or local posting issue. Fix a verified
   defect in a separately reviewed change; do not guess or weaken verification.
3. Owner-confirmed historical TEST data: the repository already has the narrow,
   audited exact-two-reference retirement workflow (`pos_test_cleanup.py`,
   `scripts.retire_pos_test_sales`). Its DEFAULT is READ_ONLY_DRY_RUN, and apply
   requires a current exact manifest hash, explicit admin confirmation and strict
   identity/payment/no-posting checks. Inspect its current runbook and output; it
   fails closed if either record/shop/status/order has changed. Do not broaden its
   allowlist or run apply under this pilot's authorization. Shopify cleanup remains
   separate. Reuse that reviewed mechanism rather than a second hidden void path.
4. If none is safe/approved, live pilot remains NO-GO. Use a separately authorized
   development shop/runtime/database. Never skip the current shop REVIEW to permit
   another CREATE, or fabricate a different shop key for the same financial domain.

No production records are changed by diagnostic preparation. This PR does not
claim the historical order now verifies or that legacy test retirement is eligible.

## Deployment and one-sale release procedure

Each mutation stage requires separate owner approval; none is performed by Codex.

1. Review PR, apply normal backup/release procedure, then migrate `alembic upgrade head`
   to `20261122_51` with pilot flag false. Confirm Phase21 test functionality unchanged.
2. Run read-only diagnostic and handle historical queue through approved choice above.
   Review ALL pending sales/operations (including Sale B), not only the named REVIEW.
   Scope/authorization cannot proceed until their financial domain checks pass.
3. Approve enabling ONLY `POS_REAL_SALE_PILOT_ENABLED=true` for the intended runtime;
   keep normal auto_receipts=false, POS_REFUND_WRITE_ENABLED=false, drawer/labels/
   isolated ingress unchanged. Do not change Shopify write/scopes as a print fix.
4. Verify exact original device `f18b74ce-cf1f-440f-973f-579640ff46e6`, Register1,
   location, current version and confirmed test `1efc47bc-db80-4f9f-91aa-29c85c11d4d1`
   (if still current/valid), POS-80 USB001,80mm, encryption/DPAPI/journal and physical
   ownership. No concurrent acceptance connector. Start only with separate permission:

   ```powershell
   Set-Location C:\PixiePinks\ReceiptConnector-190
   & .\.venv\Scripts\python.exe -B -m windows_print_agent status
   & .\.venv\Scripts\python.exe -B -m windows_print_agent run
   ```

   No pairing/rotation, journal reset, barcode queue or printer-driver changes needed.
   Reload the CRM/desktop page after deployment to load versioned pilot UI assets;
   the accepted Windows agent binary remains unchanged.
5. CRM Admin → POS → **Real-sale pilot setup · Admin**. Select verified device, enter
   scope approval notes and approve exact immutable shop/register/device. This is
   not a hardware policy save and does not enable general automatic receipts.
6. Arrange an exclusive attended cashier/configuration window. Use ONE genuine,
   approved low-value purchase: normal Register1 → scan → select payment → Checkout.
   BEFORE accepting money, click **Authorize this genuine sale for pilot**, record
   approval notes and confirm. Stop if any authorization/preflight fails. Do not
   edit cart afterward or create an artificial order merely to test printing.
7. Confirm actual cash/card receipt according to normal policy. Bank transfers need
   separate existing bank verification/finalize. Click **Confirm tender & checkout**
   once, retain POS reference and wait. Refresh recovers the SAME sale. Never collect
   payment/create a second cart after an uncertain response.
8. Require actual Shopify order read-back VERIFIED, canonical POS posting exactly once
   and financial COMPLETE, then one automatic RECEIPT job/business key. Compare real
   product, discount, tax, total, tenders/change, reference and Shopify order locally.
9. Require exactly one physical aligned/readable receipt, drawer closed, one attempt/
   AGENT_SUBMITTED_TO_PRINTER, SUBMITTED state/null spool allowed, local journal ack1.
   Physical flag remains false until honest operator confirmation in Hardware on the
   exact job ID. No duplicate browser print, no TEST watermark on the customer receipt.
10. With agent idle restart SAME profile/journal and observe several polls≥20sec with
    zero duplicates. Capture sanitized receipt/financial statuses and audits. Broader
    staff rollout/general auto policy is a SEPARATE release, not authorized by pilot.

## Incident and rollback

Stop intake on any failed preflight, timeout, lost response, order/posting mismatch,
UNKNOWN, extra paper, printer error or unexpected queue work. Preserve POS reference,
remote order ID, operation attempt, job ID, journal and diagnostic stages. Never replay
CREATE, reset attempts, force COMPLETE, delete jobs or mark paper confirmed speculatively.

For printing containment, obtain approval to Pause the selected device and stop idle
connector; an already consumed permit cannot be recalled. Turning the pilot flag OFF
holds new pilot CREATEs and future pilot claims/permits, NOT a mutation already in
flight, local recovery or unrelated normal POS work. A CREATE already sent still needs
read-back/accounting completion: finance can COMPLETE with one FAILED receipt reservation
if the print release gate is now closed. Never undo a valid payment/posting because a
printer failed. Controlled duplicate/manual recovery remains a separate explicit action
on a verified sale and inspected original job; never use it to replay UNKNOWN output.

Keep both new tables and all audit/job identities on rollback; no destructive downgrade.
Before code rollback pause/stop and inspect all pilot/test queued or uncertain jobs:
older code does not know the new pilot gate/source rules. Do not restore an older
SQLite backup or downgrade PR192's nullable spool correction. Re-enable nothing blindly.
No Railway variable/key change, live sale/order/posting, original REVIEW or device action
has been performed by preparing this implementation.

## Automated verification (disposable / mocked only)

Final results:

- Broad POS, real-pilot, preserved Phase21 TEST checkout, receipt rendering, Shopify
  adapter/read diagnostic, encrypted connector/GDI/journal, hardware, finance,
  historical test-cleanup and WhatsApp/browser regression run: **305 passed,
  0 failed, 0 skipped** (PostgreSQL fixture configured for finance coverage).
- Final pilot/checkout/no-resend reconciliation run: **58 passed, 0 failed,
  0 skipped**, including all23 final pilot cases. These overlap the broad run
  and add final admin/target-binding and conflicting-order-identity cases.
- Disposable PostgreSQL migration, scope immutability, competing checkout/worker,
  existing synthetic/hardware/POS/recovery/finance suites: **19 passed, 0 failed,
  0 skipped**. Final pilot/incident PostgreSQL recheck: **4 passed** (overlaps19).
- Python compilation, JavaScript syntax and whitespace checks passed. Existing
  deprecation warnings remain. No physical output, live order, payment or production
  database mutation was performed. Operator acceptance is still required.

The early test-only discount fixture used an unsupported edit action; it was corrected
through the existing cart-details API. Final success/timeout discount cases pass.
There are no unresolved automated failures. These tests cannot establish live
Shopify scopes/order visibility, production migration state, actual customer payment,
Windows hardware behavior or historical recovery eligibility.

Use only an explicitly disposable localhost database `pixiepinks_test` for
`TEST_POSTGRESQL_URL`, never production. Reproduce targeted verification:

```bash
python -m pytest -q tests/test_pos_real_sale_pilot.py
python -m pytest -q tests/test_pos_real_sale_pilot_postgres.py tests/test_pos_receipt_tests_postgres.py tests/test_receipt_connector_postgres.py tests/test_hardware_postgres.py tests/test_pos_postgres.py tests/test_pos_incident_postgres.py tests/test_pos_provisional_postgres.py tests/test_retail_postgres.py tests/test_finance_reporting_pos.py::test_postgres_financial_aggregation_uses_decimal_and_inclusive_dates
```
