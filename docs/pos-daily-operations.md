# Daily POS operations — Phase 23 revised scope

Daily POS only. Windows agent and all drawer implementation remain unchanged. No drawer setup, commands, tests or rollout are included. Keep `POS_CASH_DRAWER_AUTOMATION_ENABLED=false`.

## Architecture and authorization

`POS_DAILY_OPERATIONS_ENABLED=false` is the default. An administrator releases a register through **Daily POS setup · Admin**, selecting an ACTIVE receipt-only device. The policy binds current shop, register, Shopify location, device UUID and device configuration version. Every daily checkout captures an immutable policy/device binding in `pos_daily_sales`. Policy changes are versioned and audited. Staff use existing `pos.sell`, `pos.view` and register/session permissions; no per-cart pilot authorization is created. Bank transfer verification retains its existing supervisor/admin requirement. Pilot and synthetic modes retain their independent controls.

Daily opening/release requires a current attested, unpaused, empty receipt connector and a physically CONFIRMED synthetic TEST at its current configuration version. Resolve financial REVIEW/UNCERTAIN and unresolved hardware work through existing supervised controls first. This implementation neither clears historical operations nor bypasses shop-wide fences. Device/version changes invalidate authorization.

Checkout uses the existing frozen quote, verified tender, Shopify CREATE/readback and canonical accounting services. Exact order identity and linked-customer verification remain mandatory. Uncertain CREATE is never retried automatically. At financial COMPLETE, the same database transaction reserves exactly one original receipt under `receipt:auto:<checkout UUID>`. Pilot and daily bindings are mutually exclusive; the legacy automatic backfill excludes both. Renderer/device failures reserve a failed original identity for supervisor recovery without repeating or reversing the financial transaction.

Delivery uses the existing encrypted transport, immutable job payload, device version, permit, SQLite journal, acknowledgements and UNKNOWN fence. Null spool IDs remain valid submissions. Submission does not prove physical output. Ordinary daily cashiers cannot request manual browser duplicates or hardware reprints; existing POS administrators/supervisors use inspected, audited duplicate recovery. Refund controls remain unchanged.

## Migration and deployment preparation

1. Review/merge only after release approval; this branch does not deploy itself. Confirm current production commit, migration and settings through an authorized read-only runtime. No fresh production access was available during implementation.
2. Back up the database using the existing approved process. Apply additive migration `20261122_52` (parent `20261122_51`) through the normal release procedure. It creates only `pos_daily_policies` and `pos_daily_sales` plus immutability triggers. It does not update historical sales or jobs. Downgrade deliberately retains evidence.
3. Deploy with `POS_DAILY_OPERATIONS_ENABLED=false`. Preserve existing Shopify/write/encryption/local-print settings and all device policies. Keep global device `auto_receipts=false`: daily receipts use their own atomic completion hook. Keep cash drawer OFF. Do not enable pilot or isolated ingress for daily release.
4. Run in Railway Console at `/app`:

   ```bash
   python -m scripts.diagnose_pos_daily_readonly
   ```

   This uses a read-only database transaction, emits sanitized flags/identifiers/statuses, makes no Shopify/CRM/hardware calls and imports no application startup. It is not a live Shopify contract or physical-printer test. Stop on unavailable results, wrong shop/commit/migration, pending financial uncertainty, UNKNOWN hardware work or unexpected jobs. A SELECT snapshot cannot guarantee no concurrent changes.
5. Intended initial scope: shop `eb2b70`, Register 1, location `gid://shopify/Location/92690219311`, device `f18b74ce-cf1f-440f-973f-579640ff46e6`, POSPrinter POS-80 / USB001 / 80mm. Verify these live; do not infer them from this document. Review all queued CREATE consequences before any release. Never resend historical CREATE.
6. Obtain separate approval to resume the existing device and start its Windows connector if paused/stopped. Preserve DPAPI credentials, device identity and `actions.sqlite`; do not reset, copy another device journal, or run concurrent connectors. This release requires no Windows agent update or printer/driver change. Barcode printer remains separate.
7. After prerequisites and explicit feature-release approval, administrator sets only `POS_DAILY_OPERATIONS_ENABLED=true` through normal controlled Railway deployment. This permits policy setup; it does not authorize every register. In POS **Daily POS setup · Admin**, inspect exact device/register/location/version, enter approval notes, check **Authorize daily cashier checkout**, save and confirm. Refresh cashier page. Configuration refuses outstanding work or missing physical acceptance.
8. An unconfigured register retains its pre-existing behavior and flags; this is not a global POS kill switch. Restrict staff/register access using existing permissions and sessions.

## Daily cashier and shift procedure

- Sign in with individual staff account. Select authorized register; physically count and enter opening float. The software records the count, not proof that cash exists. Existing ownership prevents another cashier sharing an open shift.
- Scan barcode or search/select variant; check live price/stock; add quantity and permitted discounts. Supplier barcode variant picker is unchanged.
- Select cash/external card/bank or split tenders, enter only actual received amounts and safe external references. Never enter card credentials. Confirm once. For pending bank payments, obtain existing authorized verification before finalizing.
- Watch **Shopify**, **Accounting**, **Automatic Receipt Status**, and printer readiness separately. Hand over goods only under the existing completed-sale policy. No per-cart administrator pilot step is needed.
- Receipt prints automatically after verified financial COMPLETE. Observe paper; submission is not physical verification. **Next sale** creates a normal new cart and focuses scanner. Printing failure never means repeat checkout.
- End shift: review paid-in/out and cash sales, count physical closing cash, enter count/reason, inspect expected cash and variance. Use existing supervisor review for discrepancy and handover. Do not open another cashier session as a workaround.

## Incidents and duplicate recovery

Stop new intake on financial REVIEW/UNCERTAIN; use existing exact-order read-only reconciliation evidence. Never resend CREATE because paper did not print. Printer offline/paused retains receipt status independently from finance. UNKNOWN requires inspection and honest physical determination; never reset a journal or replay an uncertain job. Supervisor/admin reviews original job and evidence before an audited controlled duplicate. Browser refresh/repeated finalize/restart preserve original identity. A configuration revision cannot silently reroute old work.

## Separately authorized genuine-sale acceptance

No artificial Shopify sale is required. With approved normal customer transaction and cashier/session:

1. Capture read-only baseline; verify no unexpected financial/hardware queue work, matching scope, current heartbeat/attestation and global drawer OFF.
2. Scan a genuine item, confirm price/tax/discount and genuine tender once without per-cart admin authorization.
3. Record POS reference, exact Shopify order, verified payment/lines/totals/location, one canonical posting, COMPLETE, one original receipt job and `AGENT_SUBMITTED_TO_PRINTER`.
4. Observe exactly one readable aligned 80mm receipt, correct cashier/items/variant/quantity/unit price/discount/tax/tender/change/total/order reference and no TEST watermark. Drawer stays closed. Record observation honestly through existing review.
5. Under separate process-control approval, restart connector with unchanged journal and verify zero duplicates. Confirm no historical jobs, inventory outside this genuine sale, imports, WhatsApp orders or barcode settings changed.
6. Stop on any mismatch; do not retry checkout or replay UNKNOWN. Acceptance is pending until performed by an authorized operator.

## Rollback

Pause the device and stop the connector under incident approval if dispatch safety is uncertain. Set daily flag OFF through approved release; this blocks new daily checkout/finalization/CREATE dispatch and daily print permits, but cannot recall an in-flight Shopify request or physical spool submission. Do not restore an older journal or database, delete identities, undo genuine accounting, replay uncertain work or force COMPLETE. Retain migration tables and audits. Existing completed money remains completed. Review pending daily sales/jobs individually before re-enabling. Existing pilot/legacy flags remain independent. Manual supervised receipt recovery remains subject to existing financial and duplicate safeguards.

## Automated verification

Against main `c0b9cc87f72a00586e8fbbc08cd9f86172239ffa`:

- Daily service, browser, read-only preflight and release-stop tests: **27 passed** (`tests/test_pos_daily.py`).
- POS/accounting/receipt/Windows-agent/WhatsApp regression selection: **321 passed**.
- Daily concurrency/migration plus existing POS/catalog PostgreSQL selection: **8 passed**.
- Customer-readback, test-cleanup, provisional, incident, synthetic/pilot receipts, hardware/encrypted connector PostgreSQL and finance reporting selection: **19 passed**.
- **375 passed, 0 failed, 0 skipped** across the four final invocations. PostgreSQL used isolated random schemas in a disposable localhost `pixiepinks_test` database; Shopify and printers were mocked. Existing deprecation warnings remain. JS syntax and diff whitespace checks passed. No physical or production acceptance was executed.

New implementation: `pos_daily.py`; additive models/migration; completion/dispatch/receipt hooks in existing POS and hardware services; admin API/UI and cashier status/next-sale UI; SELECT-only diagnostic; daily tests and existing fixture teardown updates. No Shopify adapter, canonical receipt renderer, Windows agent, drawer driver, pulse or isolation implementation changes.
