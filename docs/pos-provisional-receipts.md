# Provisional 80 mm payment acknowledgements — opening 21 November 2026

## What this enables, and what remains blocked

Authorized POS staff can issue an acknowledgement for an existing pending checkout
with actual verified payment evidence. This is a separate document, not a final
receipt, tax invoice, local-only sale mode or Shopify recovery action.

**An existing same-shop REVIEW/UNCERTAIN operation still blocks the worker.**
Issuing this document does not allow a new queued sale to COMPLETE or verify
fulfillment/stock. Existing checkout validation, accepted-tender recording,
CREATE intent, worker fencing, reconciliation, accounting and finalized receipt
rules are unchanged. Do not collect new counter payments expecting this feature
to resolve the old queue or authorize handing over goods. Production-safe normal
completion remains unavailable until its blocker is separately resolved.

The exact old test references POS-851C4716B015DF1B0E79 and
POS-5AB126C311C38CDBD5FA are denied server-side, including after cancellation.
No cleanup/reconciliation of those records is performed by deployment or printing.
This feature does not resolve the earlier Shopify full-read error.

## Cashier behavior

1. Open an existing sale's **Sale status**, or **Sales, receipts & reconciliation**
   → **Provisional receipt / status**. Only permitted pending sales with at least
   one eligible verified tender show **Print Provisional Receipt (80 mm)**.
2. Review actual payment method, amounts and verification. Press the button and
   explicitly confirm that the listed verified funds were genuinely received.
   A bank claim is not money received. The PDF excludes unverified pending tenders
   and shows the unpaid balance; it never acknowledges those claims as paid.
3. The PDF opens or offers **Open provisional PDF** / **Download provisional PDF**.
   Chrome/Edge Ctrl+P → POSPrinter POS-80 → 80 mm roll → portrait → Actual size /
   100% → one copy. The PDF has its own margins; avoid A4, Fit/Shrink, label media,
   the label queue and the isolated drawer queue. Preview does not confirm printing.
4. Eligible provisional views keep links intact rather than polling away the
   receipt while printing. Use **Refresh sale status** to check completion; this
   reads status, never retries CREATE or records payment.
5. Reprints retain the original acknowledgement ID and first-issued timestamp and
   say COPY / REPRINT. PDF generation is audited even if the print dialog is
   cancelled. Reopening the current blob/download link does not regenerate it.
6. If additional existing tender evidence becomes verified, a later PDF retains
   the same logical acknowledgement ID, increments its payment-evidence revision,
   in the audit history; the customer copy says COPY / REPRINT. It does not record another payment.
7. After separately approved normal processing/recovery marks the **same** checkout
   COMPLETE, use the normal **Print Receipt (80 mm)** action. Its final PDF says
   it replaces the provisional acknowledgement and uses the original POS reference.
   Keep both records; do not create another checkout, payment or Shopify order to
   obtain the final receipt. Completed sales cannot issue new provisional PDFs.

The Phase 1 customer heading is INVOICE with the immediate qualification
PROVISIONAL — FINAL CONFIRMATION PENDING. It remains a provisional payment
document, not a finalized tax invoice. The footer contains the owner-approved
exchange policy, thank-you and PRYAMO contact. Product/variant,
quantity, unit price, discounts, tax and grand total come from the original frozen
quote. Only verified matched payment amounts are acknowledged; cash received and
change come from that tender's frozen snapshot. Balance is grand total less
verified payments, not a recalculation of historical sale/tax prices. Dates include
Sri Lanka timezone. The frozen cashier/customer are retained.

## Server safety and audit contract

`POST /crm/pos/api/sales/{checkout_uuid}/provisional/pdf` takes exactly
`{"confirm_received":true}`. It uses existing authentication, CSRF, same-shop lookup,
`pos.sell` + `pos.view`/administrator authorization and the existing worker lock.
A read-only eligibility hint in sale/history payloads is advisory; generation
rechecks under the lock. There is no public anonymous PDF URL or automatic printer
job. Responses are private/no-store PDF documents.

Permitted pending states: AWAITING_PAYMENT, PAYMENT_READY, QUEUED, REVIEW, UNCERTAIN
and CONFLICT. Both known test references, cancelled/complete/unsupported states,
invalid frozen hashes, mismatched/non-POS commercial identity, rejected tenders,
missing verification actor/time and any local refund/REFUND-operation activity
are denied. Known observed Shopify refunds/voids are denied too. This document
makes no new remote API call and cannot detect an external Shopify edit that has
not been observed locally; its non-final warning is deliberate.

Every linked payment must correspond to its immutable original tender by
idempotency key, method, amount, currency/source and reference. Only positive
VERIFIED payments with verification actor/time are included. Cash/card additionally
require original receipt confirmation; cash received minus change must match the
applied payment. At least one verified payment is required and verified totals
cannot exceed the frozen grand total. Partially verified mixed tenders show only
received amounts, not the pending remainder. Staff's explicit funds confirmation
is audited; the software cannot physically inspect a till or terminal settlement.

Logical ID is `PPA-<original POS reference>`. Existing POSAudit rows record
PROVISIONAL_ISSUED / PROVISIONAL_REPRINTED / PROVISIONAL_UPDATED, actor ID,
first/copy issue times, revision, payment IDs/evidence hash, verified total/balance,
frozen hash and actual-receipt confirmation. Rendering succeeds before document
audit commit. Concurrent issuance under the PostgreSQL shop lock produces one
original and one reprint. No new financial tables or transaction rows are created.

Issuance appends document audit only. It never creates/changes checkout status,
SalesOrder, payment verification, cash movements, CREATE/refund/fulfillment intent,
Shopify order, stock, accounting posting or hardware job. No private customer or
payment payload is sent to an external printer service. Existing finalized receipt
COMPLETE/refund payout checks remain intact, with a provisional cross-reference
added when applicable. Hardware agent receipt generation still uses only the
finalized generator; no provisional hardware route is added.

## Deployment — separate release approval

Review the draft PR and test evidence before merge/release. This task neither
merges nor deploys. No migration, new environment variable, dependency, Shopify
scope or desktop application build is required. Keep all existing write/printing/
ingress flags unchanged; do not enable automatic printing or drawer controls.

After an approved normal Railway code release, verify its commit SHA and reload
the desktop POS/browser so the new versioned JS assets load. No document or payment
is generated on startup. The UI is served by the existing CRM; if the Windows
shell cannot display the PDF, use the same authorized CRM URL in Chrome/Edge.
Default final receipts remain COMPLETE-only. Resolve actual deployment issues
through the existing release process, not by weakening guards.

## Physical printer acceptance — authorized operator only

No physical output or Windows configuration is performed by development tests.
Use [the clearly synthetic provisional sample](evidence/pos-provisional-80mm-sample.pdf)
for the local layout check; it says SAMPLE / NOT A SALE and creates no production
sale/payment/audit. Never use either designated test checkout for customer PDFs.

- Confirm the actual installed POSPrinter POS-80 driver, correct USB/DOT4 binding,
  online state and 80 mm thermal roll. Do not guess a USB port or replace drivers.
- Use the driver's supported 80 mm roll/custom form, portrait, 100% / Actual size.
  The PDF is 80 mm wide, 4 mm side margins and measured variable height; do not fit
  to A4 or choose a label form. Validate the driver's actual paper-length behavior.
- Verify the legible provisional qualification under INVOICE; long English/Sinhala names,
  quantities/unit prices, discounts/tax, verified totals, balance, cash/change and
  footer are readable and unclipped. Check actual paper/cut/feed, not just preview.
- Keep the normal receipt driver's Cash Drawer=None. Driver cashbox pulses are
  independent of CRM flags. Do not select the isolated drawer queue or test a kick.
- Test popup-blocked PDF links and a COPY/reprint using synthetic local acceptance
  data only. For any real receipt, require real funds and release approval.
- Verify normal final receipt and later cross-reference using synthetic completed
  fixtures, not forced production COMPLETE states. Retain sample copies distinctly
  from genuine customer acknowledgements.

## Rollback

Revert the feature code through the approved deployment process and reload the
POS browser. No migration or data rollback is needed. Preserve all prior document
audits/PDFs and their references. Do not delete/modify payments, cash, journals or
Shopify records to roll back document printing. Removing the UI cannot revoke a
paper acknowledgement already issued; finalization must still use the original
checkout and separately verified process. Existing final receipt safeguards and
WhatsApp Sales Orders remain operational.

## Verification report

- Main regression run: 311 passed, four PostgreSQL-only skips. Includes provisional
  server/browser behavior, existing final receipts, POS/queue/reconciliation and
  test-cleanup guards, finance, WhatsApp Sales Orders/payments/Draft Orders/order
  lifecycle, barcode and inventory tests.
- PostgreSQL provisional concurrency and existing POS/recovery suites: 7 passed.
- PostgreSQL finance aggregation: 1 passed separately.
- The three remaining skipped PostgreSQL payment/Draft Order/webhook concurrency
  cases: 3 passed separately in fresh local UTF-8 databases. A shared legacy test
  database initially caused two fixture FK-cleanup errors; isolated per-case DBs
  eliminated that test-data interference without changing production code/tests.
- Additional long Sinhala/English, discounts/tax and 80 mm geometry case: 1 passed.
- Total distinct executed passing cases: 323. All four initial skips were executed
  successfully with PostgreSQL. JavaScript syntax and git diff checks passed.
- Browser confirmation cancel/accept, popup fallback, retained links, history
  reprint and absence of hardware/financial effects are covered. A synthetic PDF
  was visually inspected; actual Windows printing remains operator acceptance.

No live Shopify API, production financial operation, old-test cleanup, deployment,
Railway variable or physical printer command was used during verification.


The customer presentation above is updated by [Receipt branding Phase 1](pos-receipt-branding-phase1.md). Internal issuance timestamps, evidence, revision and reprint audits are preserved. Detailed printer alignment remains deferred.
