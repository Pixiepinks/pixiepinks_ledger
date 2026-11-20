# Phase 4G implementation report

## Scope and changed files

- `pos_receipts.py`: canonical professional monochrome layout, exact measured roll pages, Unicode wrapping, aligned snapshot amounts, configured contact/legal/tax details, separate refund receipts and existing success audit/duplicate semantics. No financial calculation/checkout/payment/inventory/Shopify logic changes.
- `static/pos_receipt_print.js`: browser preview controller with synchronous preview reservation, opener isolation, blocked/closed-window link fallback, PDF validation, download, useful errors and no physical-print success claim.
- `static/pos.js`, `static/pos.css`, `templates/pos.html`: prominent completed-sale/refund print actions, 80 mm default plus optional 58/A4, existing history workflow, styles and static cache version update.
- `tests/test_pos_receipts_phase4g.py`, `tests/test_pos_receipt_browser_phase4g.py`: PDF geometry/content, Sinhala, financial snapshot display, authorization/refund state guards, browser/history recovery and canonical hardware compatibility.
- Documentation/README and `docs/evidence/phase4g-80mm-sample.pdf`: cashier/physical acceptance/rollback guide and clearly synthetic review artifact.

Before: estimated-height, mostly left-aligned receipt text and a generic preview action. After: centered brand, measured content-height 80 mm PDF, wrapping product/variant lines, aligned LKR amounts, explicit discounts/configured tax, emphasized total and prominent 80 mm browser print workflow. 58 mm and A4 remain functional.

## Compatibility and safety

The existing authorized receipt endpoint body/response, `generate` signature/kwargs, future `hardware_service.receipt_job` integration, job identities, profiles, queue routing, idempotency and duplicate behavior are preserved. Tests compare browser/hardware document content apart from the expected duplicate marking and verify audits, frozen snapshots, journal/payment counts and no browser-created drawer/hardware jobs.

No changes to financial/Shopify/inventory/customer/barcode logic, Print Agent/ingress/pairing security, flags or Windows configuration. No migration/dependency changes. No live financial, Shopify, print or drawer actions, production variable changes, merge or deployment occurred. Existing device security/no-replay regressions remain included. Manual Windows driver cashbox behavior is explicitly documented as independent of CRM rules; no claim is made that manual reprints keep it closed.

## Validation

- Focused PDF/browser/POS/scanner/hardware suite: **68 passed, 0 failed**.
- PDF tests cover exact width/zero-origin page boxes, content-dependent short/long heights, no blank trailing page, glyph bounds within the page, long/unbroken names, Sinhala fonts, varying quantities, discounts/tax/customer/time details, cash/card/bank/split tenders, all nonfinal state rejection, confirmed refund payout and 58/A4.
- Chromium tests cover popup blocking/success, HTTP/state errors, invalid PDF/HTML responses, network failures/retry, optional format requests, no physical-success claim, CSRF and Sales History duplicates without agent connection/dispatch.
- Python/JavaScript syntax checks and whitespace checks passed. 203 dpi synthetic sample and browser workflow visually reviewed. Actual DBL/Windows output, cutter/feed/driver form and physical drawer behavior remain unverified.
- Broad POS/accounting, receipt/browser, hardware/agent/ingress security, Retail/scanner, inventory, Shopify/payment/fulfillment, staff and WhatsApp regression: **497 passed, 2 skipped, 0 failed**. Both PostgreSQL-engine-only skips passed separately on disposable PostgreSQL (**1 Draft Order concurrency + 1 fulfillment concurrency**). Hardware/POS/inventory PostgreSQL tests also ran in the broad suite. Existing deprecation warnings remain. Runs overlap; do not sum their counts as unique tests. All test databases are disposable and external interfaces mocked.

[Cashier guide, physical verification and rollback](pos-phase4g-receipts.md) · [Synthetic 80 mm sample](evidence/phase4g-80mm-sample.pdf)
