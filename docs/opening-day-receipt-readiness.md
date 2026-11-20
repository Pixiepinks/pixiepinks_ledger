# Counter receipt readiness — opening 21 November 2026 (Sri Lanka)

This is the pre-implementation readiness assessment. The separately reviewed [provisional receipt feature](pos-provisional-receipts.md) implements the document-only proposal below. The same-shop queue limitation remains unchanged.

## Decision

Inspected latest main `bc96d2c` (PR #185 merged); this is source evidence, not
proof of the current Railway deployment. No production connection, Windows PC
or physical printer is attached to this development environment.

Manual 80 mm receipt generation/browser delivery is implemented. It requires
COMPLETE and uses the finalized frozen snapshot. The shop worker intentionally
refuses all queued work when any same-shop operation is DISPATCHING, UNCERTAIN
or REVIEW. If A is still REVIEW in the live configured shop, leaving it untouched
means a new checkout cannot complete normally. Changing print settings or adding
a provisional PDF does not resolve that queue or verify the Shopify full read.
Do not take another POS payment expecting a finalized receipt from this state.

No existing local-only counter completion/receipt mode was found. General CRM
Sales Orders supports local orders and audited payment claims/verification without
automatic journal posting. It is not a substitute for finalized POS receipts,
stock processing or the POS register, and existing POS records explicitly reject
payment changes through that separate workflow. Do not duplicate a POS sale as a
WhatsApp SalesOrder or create a Shopify Draft Order merely to print.

Old A/B test transactions remain untouched. Do not run the retirement utility,
reconcile, retry, change statuses, remove the queue fence or change write flags
as part of this readiness check.

## Tonight: physical printer acceptance, no sale required

1. On the cashier PC open Settings → Bluetooth & devices → Printers & scanners →
   **POSPrinter POS-80** → Printer properties. Inspect the existing tested driver
   and actual USB/DOT4 port. Do not select a port by guessing its number or replace
   the driver based on the queue name. An administrator can inspect without change:

   ```powershell
   Get-Printer -Name 'POSPrinter POS-80' | Select-Object Name,DriverName,PortName,PrinterStatus
   Get-PrintConfiguration -PrinterName 'POSPrinter POS-80' | Select-Object PaperSize
   ```

2. Load 80 mm thermal receipt stock and use the supported 80 mm roll form in that
   driver. This PDF is 80 mm wide with 4 mm side margins (72 mm content width).
   Use portrait, Actual size / 100%, one copy. Do not use A4, 58 mm, Fit/Shrink,
   extra application margins, the label queue or the isolated drawer queue.
3. The existing Windows setup guide records Cut paper per page, Semi Cut and
   15 mm feed after print as the owner's tested preferences. Inspect the actual
   supported controls; do not claim these are portable driver defaults. Keep the
   normal receipt queue's **Cash Drawer=None**. A driver drawer kick is independent
   of browser/CRM flags; do not test the drawer or agent.
4. Obtain `docs/evidence/phase4g-80mm-sample.pdf` from reviewed main. It is a
   single-page SAMPLE / NOT A SALE PDF, measured 79.999 × 154.901 mm. Open it in
   Chrome/Edge, Ctrl+P → POSPrinter POS-80, Actual size / 100%, one copy. If the
   driver needs a custom form, use a supported 80 mm width and enough page length
   for the PDF (about 155 mm for this sample); final receipts have variable height.
   Do not crop or fit the sample to a label. Use an authorized local print check.
5. Inspect actual paper: full title and footer, quantities/prices, unclipped LKR
   total, readable text, feed/cut and no unintended drawer action. A successful
   preview/spool entry is not physical acceptance. Fix local paper/form/driver
   mismatch before customer service; no Railway variable enables browser printing.
6. When a genuinely COMPLETE sale is available, the normal UI is **Print Receipt
   (80 mm)** → **Open receipt PDF** if a popup is blocked → browser Print. Print the
   PDF, not the CRM webpage. Generation/reprint audits do not record another
   payment/order; a newly generated second PDF is marked DUPLICATE / REPRINT.

If the Windows desktop shell cannot display a PDF or print dialog, use the same
CRM address in installed Chrome/Edge with the same authorized login. No new agent,
certificate or ingress activation is required. The desktop shell itself was not
remotely tested here. Do not update/install automation merely for browser printing.

## Read-only Shopify diagnosis

After verifying PR #185 code is present in the authorized Railway runtime, from
its repository directory:

```sh
python -B -m scripts.diagnose_pos_order_readonly \
  --order-id gid://shopify/Order/7610874233135 --groups full
```

If the full read fails:

```sh
python -B -m scripts.diagnose_pos_order_readonly \
  --order-id gid://shopify/Order/7610874233135 \
  --groups identity,totals,customer,lines,transactions,fulfillment,refunds
```

Return sanitized JSON only. HTTP/GraphQL code/path, returned API version, query
cost and failing group distinguish permissions/schema/cost/transport causes.
The exact production cause cannot be claimed until this output is available.
Partial-group success is not canonical verification or permission to issue a
finalized receipt. These commands do not modify records or start the worker.
Do not run the cleanup or reconciliation commands in the same session as a
supposed printing fix. No new credentials or variable dump is required.

## Opening-day contingency if completion remains unavailable

The fastest no-code contingency is a separately owner-approved numbered manual
**PROVISIONAL PAYMENT ACKNOWLEDGEMENT — NOT A FINALIZED / SHOPIFY-VERIFIED RECEIPT**,
issued only after staff actually receives and confirms the funds. It is a proposed
manual operating procedure, not an existing CRM feature or authorization to record
production payments in this task. If the shop requires a system-finalized receipt
for every purchase, current same-shop POS operation is not ready while A blocks.

Maintain one controlled transaction register and one customer copy: unique
acknowledgement number, Sri Lanka time, item/SKU/quantity, actual paid amount and
method/reference, cash received/change if applicable, and responsible cashier.
Where a POS checkout already exists, retain its exact POS reference on both
copies; do not create a second checkout or take/record the tender again. For an
entirely manual transaction use one unique manual reference, preserve it for later
one-time approved entry, and do not also create a queued POS copy. Reprints keep
the number and say COPY. Reconcile actual cash separately; no invented software
accounting or stock success is implied. Physical handover/stock availability needs
manual control because this acknowledgement does not decrement Shopify stock.

Do not later enter manual transactions into POS blindly: its current checkout
uses current prices, stock and date and would initiate new remote intent/tenders.
An administrator must approve an explicit one-time migration/reconciliation plan,
search existing remote/local references first and ensure the already received
funds are not collected or recorded twice. Do not promise an automatic final
receipt conversion for manual-only transactions; no such importer exists.

## Smallest provisional PDF proposal (requires review/deployment)

No new receipt code or financial workflow is implemented by this readiness work.
If an integrated 80 mm acknowledgement is required, use a distinct endpoint/UI
and document type, not an exception in `pos_receipts.generate`:

- Read one existing checkout and its verified, un-rejected linked tender records;
  require exact frozen identity, current active staff permissions, actual received
  funds confirmation and a fully verified amount matching the acknowledgement.
  Unverified bank/card claims and known test A/B must be ineligible. A database
  VERIFIED marker alone cannot establish physical cash; staff confirmation matters.
- Print the explicit provisional heading at top and bottom, original POS reference,
  acknowledgement identity, paid amount/method and pending system status. Never
  print “Shopify verified”, final receipt identity or mark COMPLETE.
- The action may append a dedicated document audit after successful render under
  the existing lock. It must not create a checkout/payment/cash movement, journal,
  remote order, refund, stock adjustment, hardware job or change queue state.
- Reprints reuse the same logical acknowledgement identity and say COPY, with
  audit. This document cannot solve the blocked queue or authorize releasing goods
  based on an unreceived payment. Finalized receipt eligibility remains unchanged.
- Once the *same* checkout is verified COMPLETE through approved normal recovery,
  generate the existing final receipt with a cross-reference to the provisional
  document. Retain both histories and label it as replacing the acknowledgement;
  do not create a replacement transaction, tender or CREATE request.
- Tests must prove no financial/remote effects, failed-render behavior, exact
  payment authorization, rejection of test/unpaid/cancelled states, stable reprint
  identity, concurrency and safe final-document linkage. Review/deploy separately;
  no flag or migration should be invented without an actual reviewed design.

This proposal helps document an existing received payment; it is not a local POS
completion implementation. For genuine local-only counter sales, a separate
accounting/inventory/idempotency design is required, not an overnight COMPLETE
bypass. WhatsApp Sales Orders and finalized receipt guards remain unchanged.
