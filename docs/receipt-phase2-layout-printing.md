# Phase 2 — 80 mm receipt layout and manual printing

## Confirmed findings and limits

Inspected main `4ef7432` (merged Phase 1 branding). No Windows/printer or authorized
production runtime was attached to the development environment. The reported
physical clipping and Chrome blank tail cannot be diagnosed conclusively without
the actual driver form, scale, printable width and paper measurements.

The original shared layout used 4 mm roll side margins and ruled lines at x=4–76
mm. Those strokes extend outside the new 70 mm safety region (x=5–75). `pair()`
reserved a different value-cell width for each string and point size, relying on
implicit cell padding rather than one fixed numeric column and explicit safe
profile. Its oversized-value behavior could reject a value instead of wrapping.
These were verified geometry weaknesses. **The old sample itself did not show
out-of-page amount clipping or an A4-sized blank tail.** Its text ended at x=74.964
mm with about 6.127 mm from last footer ink to page bottom. Do not report an
unverified printer/scaling cause as a proven application defect.

The patch supplies a deterministic PDF safety contract, not driver configuration
or physical-printer certification. Extra paper from a fixed driver form or
post-print feed is separate from PDF page height and must be accepted locally.

## Geometry and typography

- Nominal 80 mm page; 5 mm left/right margins, 70 mm usable region and 4 mm top.
  Text cell padding places typical glyph ink around x=6–74 mm. Rule endpoints
  are inset so their stroke thickness also stays inside x=5–75.
- One 25 mm money column with the same right anchor throughout. Actual shaped
  font widths determine fit. Labels wrap in the remaining column with a 3 mm gap.
  80 mm monetary rows use at least 9 pt; totals keep their existing emphasis.
  Noto's tabular-number feature is explicitly enabled.
- Oversized amounts move below their label at the same right anchor. Exceptional
  unbroken values wrap by character rather than clipping, truncating or shrinking.
  Decimal/LKR formatting and every stored amount remain unchanged.
- Invoice No: and the exact original POS reference occupy separate lines. Long
  references/names/Sinhala/payment references use measured wrapping.
- Contacts use 9 pt, INVOICE retains 15 pt, the 9 pt provisional qualification wraps
  naturally. COPY / REPRINT remains visible. Phase 1 words/policies are unchanged.
- Consistent, compact 80 mm divider spacing; provisional bottom allowance 4.5 mm
  after the layout cursor (actual last-ink gap measured below). No PDF cropping.
- Both height passes use the same class, font metrics, shaping, logo, margins,
  widths and wrapping. Short/long receipts remain one content-height page, with
  the existing maximum-length guard. No fixed A4 height is introduced.

The shared ReceiptLayout geometry also serves finalized/refund PDFs and optional
58 mm/A4. Their content, guards and page widths are unchanged; the shared fixed
column/wrapping/tabular features and slightly inset rules are regression tested.
Only 80 mm gets the 5 mm margin, 4 mm top and 9 pt minimum amount-row font. Final
receipt bottom allowance retains its existing behavior. No finalized receipt is
permitted before COMPLETE, and refund payout guards remain unchanged.

## Original-art thermal derivative

Original `static/images/login_logo.png` remains byte-for-byte unchanged.
`static/images/receipt_fairy_203dpi.png` is a local 153×208 pixel, one-bit
black/white derivative rendered at 26 mm high and horizontally centered.
Transparent padding is cropped by the original alpha bounds, composited on white,
gray contrast enhanced and resized/diffused at 203 dpi. It is not generated or
redrawn; there is no external image dependency. There are no faint gray pixels in
the derived PNG. Physical dot gain/thermal darkness still requires acceptance.

Reproduce with `python -m scripts.build_receipt_thermal_logo`. The deterministic
script is not called by application startup or printing. Tests compare rebuilt
pixels to the shipped file. If absent, the original color asset is the fallback;
if unavailable, PixiePinks text remains. Original-art RGB/alpha tests still cover
that fallback. No login branding or finalized wordmark is replaced.

## Before/after sample measurements

Same synthetic sale, products, quantities, amounts and Phase 1 business wording:

| Measurement | Before | After |
| --- | ---: | ---: |
| Nominal width | 80 mm | 80 mm |
| Serialized PDF width (rounding) | 79.999 mm | 79.999 mm |
| Page count | 1 | 1 |
| Content-sized page height | 242.602 mm | 239.399 mm |
| Leftmost text ink | 4.999 mm | 6.001 mm |
| Rightmost text ink | 74.964 mm | 73.962 mm |
| Final footer ink to page bottom | 6.127 mm | 5.679 mm |
| PDF bytes | 2,226,624 | 23,547 |

[Sample PDF](evidence/pos-provisional-80mm-sample.pdf) is marked SAMPLE / NOT A SALE.
[Machine-readable evidence](evidence/receipt-phase2-geometry.json) records these
values. A 203 dpi raster was visually reviewed. The smaller file comes from the
purpose-built mono asset, not loss/removal of words, financial data or safeguards.

## Exact browser settings

After separately approved code release, reload the POS to load the versioned
printing-help asset. Generate only a permitted document for the existing checkout.
Open/download the PDF (not the CRM HTML page), then Chrome/Edge PDF viewer Print:

1. Destination: **POSPrinter POS-80**, never the label queue or isolated drawer queue.
2. Paper: the installed driver's supported **80 mm thermal roll** form; orientation
   **Portrait**; scale **Actual size / 100%**; one copy, one PDF page per receipt.
3. Do not use A4, Fit, Shrink-to-printable-area, extra page margins or multiple pages
   per sheet. Browser web-page headers/footers must be off if offered; the PDF
   viewer normally has no such header/footer control. Retain the receipt's own
   business footer. The PDF already includes its safety margins.
4. Inspect the preview for the entire right column and PRYAMO phone before print.
   Browser/driver control labels vary; record the actual selected form/scale.
5. Click Print manually. PDF creation/spooling does not prove physical output.
   No silent printer selection, agent pairing, raw USB or drawer command is added.

## Windows POSPrinter POS-80 acceptance

An authorized operator must perform the following on the real counter computer.
This task executes no physical output or settings changes.

- Settings → Bluetooth & devices → Printers & scanners → POSPrinter POS-80 →
  Printer properties: inspect the installed tested driver and actual USB/DOT4
  binding. Do not guess USB001 or replace the driver just from the queue name.
  Read-only inspection:

  ```powershell
  Get-Printer -Name 'POSPrinter POS-80' | Select-Object Name,DriverName,PortName,PrinterStatus
  Get-PrintConfiguration -PrinterName 'POSPrinter POS-80' | Select-Object PaperSize
  ```

- Printing Preferences/Printing Defaults: verify supported 80 mm roll/custom form,
  portrait and no driver scaling. Confirm at least a 70 mm printable region. If
  the driver/device only supports 68 mm, record that limitation and obtain a
  reviewed 6 mm-margin profile; do not hide it by shrinking customer receipts.
- Keep the normal receipt queue's **Cash Drawer=None**. Do not choose or test the
  isolated drawer queue. Driver cashbox pulses are independent of CRM flags.
- Retain verified cutter settings. Record the current post-print feed; the earlier
  installation runbook used 15 mm. That can add physical paper beyond the PDF's
  own ~5.7 mm footer clearance. Do not crop the PDF to counteract driver feed.
  Any feed/cut adjustment must use manufacturer-supported settings and an
  authorized local check that the full footer clears the cutter safely.
- If the driver only exposes fixed-length forms, use a supported custom form or
  compatible roll mode. For this sample the PDF is ~239.4 mm long; a supported
  80×240 mm sample form can be tested. That is not a fixed length for all receipts.
  Future long receipts need matching roll/custom length. Do not force all PDFs
  onto this sample length or A4; extra form length produces extra paper.
- Print the synthetic sample once at 100%. Measure paper width, left/right clear
  areas, complete decimals/digits, footer clearance and cut/feed. Compare the
  physical blank tail to PDF page properties and configured feed separately.
- Inspect the logo at actual size for recognizability/dot density. Test a long
  reference, long English/Sinhala names, several quantities, discounts, mixed
  payments, grand total and a copy using synthetic acceptance data. Check all
  digits and policies; no overlap, second page or cropped footer is acceptable.
- Only accept real printing after this physical check. Do not create test
  production transactions, force COMPLETE, reconcile or clean up the two old
  test records to produce a sample. Desktop PDF limitations can use the same
  authorized CRM in installed Chrome/Edge; no new agent is needed.

## Release, safety and rollback

Create/review the draft PR; no automatic merge or deployment. No migration,
dependency, Railway variable, Shopify scope, POS state/payment/accounting/register/
queue/refund/stock behavior or permissions change. Provisional evidence/issuance/
availability and finalized generation guard functions are AST-identical to main.
The two designated test exclusions remain. Printing does not unblock the old queue
or resolve Shopify read-back. WhatsApp Sales Orders remain unchanged.

After an approved deployment verify commit SHA, reload POS, and perform only the
separately authorized physical acceptance above. No automatic print-agent ingress
or cash-drawer activation. Rollback restores layout/assets/help through the normal
approved release and browser refresh. Preserve audits, downloaded PDFs and all
transaction history. A code rollback cannot change previously printed paper; do
not delete records, resend CREATE or reverse finances to undo a layout change.

## Automated verification results

- Final receipt/geometry/POS/provisional/browser/WhatsApp Sales Order/Shopify run:
  **210 passed, 3 skipped** (legacy PostgreSQL integration cases requiring their
  separate test databases).
- Local PostgreSQL concurrent original/reprint audit test: **1 passed**.
- Final geometry module rerun, including added mixed-payment position coverage:
  **11 passed**, of which ten overlap the main run. Total distinct passing cases:
  **212**. Earlier focused receipt/layout run: 60 passed (overlapping).
- Geometry tests inspect actual PDFium character boxes, amount right edges, label
  collisions, centered image matrices/contact ink, line paths, inter-product gaps,
  complete exact long references/digits, short/long one-page sizes, identical
  two-pass end positions and actual 203 dpi white margin/footer raster bands.
- The monochrome asset is rebuilt deterministically and compared by pixels, with
  one-bit contrast, pixel height/DPI and original-source preservation checks.
  The original RGB/alpha fallback and all Phase 1 literal-content tests remain.
- Finalized/refund/58 mm/A4 rendering and browser manual delivery remain covered.
  AST comparison confirms eligibility, availability, audit/issuance and finalized
  guard functions unchanged. JS syntax and `git diff --check` passed.
- A 203 dpi sample raster was visually reviewed. Physical device output, actual
  driver printable width and feed/form configuration remain operator acceptance.
