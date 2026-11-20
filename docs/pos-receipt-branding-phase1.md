# Receipt content and branding — Phase 1

Presentation-only update to the provisional customer PDF. Detailed column alignment,
clipping, printable-width calibration and Windows driver changes are explicitly
out of scope and deferred to Phase 2. Existing 80 mm page construction, margins,
font infrastructure and shared finalized/refund rendering are unchanged.

## Original logo

The existing owner-supplied fairy artwork is `static/images/login_logo.png`
(2000 × 2000 RGBA). It visually matches the supplied design and is reused directly;
no new drawing, image editing, recoloring, cropping or external dependency is used.
SHA-256: `1e7a496e574b9f93d77fbc77b31c83a87e6b60c3634ff07a35c349bf9958685b`.
The unchanged PNG is embedded centered in a 32 × 32 mm box with preserved aspect
ratio. RGB and alpha pixels are retained in the PDF, including its transparency
mask. Missing/unreadable artwork falls back to centered `PixiePinks` text. Existing
login branding and finalized receipts are not changed. A thermal printer may
render colors monochromatically; no color conversion or driver change is made here.

## Exact customer-facing content and order

```text
[Original PixiePinks fairy logo, or PixiePinks fallback]
Address: 213/5, Magammana, Homagama
info@pixiepinks.shop
0705755777
www.pixiepinks.shop
INVOICE
PROVISIONAL — FINAL CONFIRMATION PENDING
[COPY / REPRINT on later copies/revisions]
--------------------------------
Sale recorded: [original date/time in Sri Lanka]
Cashier: [original frozen cashier]
Customer: [original customer or Walk-in Customer]
Invoice No: [original stable POS reference]
--------------------------------
ITEM / QTY × UNIT PRICE
All amounts in LKR
[Original product names, variants, quantities, unit prices and line amounts]
[Original item/order discounts when applicable]
--------------------------------
Subtotal [frozen amount]
Total discount [frozen amount]
[Original configured tax description, or Tax] [frozen amount]
GRAND TOTAL LKR [frozen amount]
--------------------------------
VERIFIED PAYMENTS ACTUALLY RECEIVED
[Verified method and amount only]
Cash received [original frozen cash received, for verified cash only]
Change [original frozen change, for verified cash only]
Verified received LKR [verified total]
Remaining balance LKR [balance]
[If balance > 0: Pending/unverified tender claims are NOT acknowledged as received.]
--------------------------------
Mode of Payment: [Cash / Card Payment / Bank Transfer]
[For multiple verified methods: Mixed — method / method]
No of Item sold: [sum of original frozen quantities]
--------------------------------
Items in Original Condition can be Exchanged
within 3 days along with the receipt.
All purchases are non-refundable.
No cash refund.
--------------------------------
Thank you for shopping @ Pixiepinks
--------------------------------
Powered by PRYAMO
0703322111
```

Existing financial amounts and labels in the transaction section are preserved.
The payment summary lists only methods in the verified evidence, deduplicated in
original tender order. The item count sums quantities, not product lines, and is
not a new sale/fulfillment assertion. The exchange/refund policy is owner-supplied
display text only; actual refund/return permissions and workflows are unchanged.

Visible verbose acknowledgement/first-issue/copy-issue metadata, oversized warning
blocks and the technical paragraph are removed as requested. First-issue time,
logical PPA ID, original reference, revisions, evidence hashes, actor confirmation
and all issuance/reprint audits remain internal and unchanged. Every reprint or
later payment-evidence revision displays COPY / REPRINT; revisions are not new
invoice identities. INVOICE is visibly qualified as provisional immediately below
the heading, not represented as a finalized tax invoice. No new compliance/tax
registration claim is introduced. The incomplete checkout still cannot issue the
normal finalized receipt.

## Review artifact and tests

[Updated synthetic PDF](evidence/pos-provisional-80mm-sample.pdf) has SAMPLE / NOT A
SALE product text and a synthetic reference. It is not a customer transaction.
Tests verify original asset SHA and embedded RGB/alpha bytes, logo fallback,
ordered exact contact/header/policy/footer text, stable original reference/time,
customer fallback, cash/card/bank/mixed method summaries, total quantity, frozen
amount preservation, reprints/revisions, pending/unverified/test/refund exclusions,
authorization/CSRF, audit-only behavior and unchanged final receipt safeguards.
Existing POS and WhatsApp Sales Order/Shopify regressions are run with synthetic
data; no live financial operation or physical print is performed.

## Release and rollback

Review the draft PR and synthetic PDF before separately approved merge/deployment.
No migration, new environment variable, dependency, Shopify scope, Windows app
build or API/UI route change is needed. The server uses the existing local logo;
no CDN fetch occurs. No print agent, ingress, cash-drawer or write flag is enabled.
After an approved code release, newly generated provisional PDFs use this content;
already downloaded PDFs/paper copies retain their original content. Rendering a
new copy retains the same stable reference and records the existing reprint audit.

Rollback restores the prior presentation code through the approved release
process. Preserve existing document/audit/payment/checkout history. Never delete
financial or audit rows to undo a branding change. The two historical test sales
remain server-side denied and are not altered by this task. The existing blocked
Shopify queue/full-read issue remains an independent operational blocker.

## Validation results

- Final receipt/POS/provisional/browser/WhatsApp Sales Order/Shopify regression
  run: **200 passed, 3 skipped**. The skipped cases require separate legacy
  PostgreSQL integration databases; no live connection was used for them.
- Real local PostgreSQL concurrent provisional issuance: **1 passed**, preserving
  one original, one COPY / REPRINT and the same internal audit identity/time.
- Earlier focused content/logo/provisional/finalized run: 50 passed (overlaps the
  final regression selection and is not counted again).
- Exact source asset SHA, embedded RGB/alpha equality, missing-logo fallback,
  cash/card/bank/mixed modes, frozen quantity count and ordered literal content
  are covered. Existing state/auth/refund/test-denial/no-financial-effect tests
  remain in place. The synthetic PDF was visually reviewed.
- `git diff --check` passed. Shared finalized renderer, routes, checkout/service
  code and the source logo are byte-for-byte unchanged from main.
