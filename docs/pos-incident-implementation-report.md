# POS post-CREATE failure investigation / implementation report

Base: main **0fd48c3**. Owner-supplied incident: A `POS-851C4716B015DF1B0E79` REVIEW / READBACK_OR_POSTING_UNAVAILABLE; B `POS-5AB126C311C38CDBD5FA` QUEUED; two verified LKR 350 cash tenders. Checkout writes confirmed true. The owner subsequently confirms A is visible in Shopify under its exact POS reference. No authenticated live API connection is available here, so no independent field-level API verification or final historical root-cause assertion is claimed.

Confirmed phase: CREATE returned an ID, then read/verification/local posting failed. A's REVIEW operation blocks subsequent jobs in the same shop. Expected cash and completed-sales totals intentionally reflect different stages. Root cause within A's generic error remains missing live evidence; the safe read-only probe supplies the next step.

## Changed files

- `pos_failure_codes.py`: fixed allowlisted codes/stages; no raw exception/SQL/customer/token data.
- `pos_service.py`: preserve post-CREATE top-level code/known remote ID; add precise immutable failure audit/public diagnostic; explicit reconciliation savepoint/409; fix interrupted QUEUED badge to UNCERTAIN without altering attempt fences. Strict verify_order and order_wire/financial calculations remain unchanged.
- `static/pos.js`, `templates/pos.html`: truthful reconciliation/local-accounting confirmation, safe code display and cache version update.
- `scripts/inspect_pos_sales_readonly.py`: existing-auth READ/FIND-only transport, read-only DB transaction, sanitized evidence for both selected references. No dispatch/publication/reconciliation/write method.
- `tests/test_pos_incident_diagnostics.py`, `tests/test_pos_incident_postgres.py`, `tests/test_pos_reconcile_ui.py`: queue blocking, query-only inspection, exact verification rejection, single recovery, real PostgreSQL partial-posting rollback, no resend and confirmation checks.
- `docs/pos-sale-readback-recovery.md`: findings, authorized live probe without new credentials/deployment, conditional recovery, automatic B-release warning, configuration assessment and rollback.

No migration, dependency, Railway variable, Shopify mutation wire, verification-rule, print-agent, barcode, pricing/tax/discount, stock-adjustment or cash-payment creation changes. No live recovery, print, mutation, merge or deployment.

## Verification

- Focused POS/diagnostic/receipt run: **51 passed**.
- Focused diagnostic/reconciliation/POS run after savepoint changes: **32 passed**.
- Incident-specific PostgreSQL/browser/diagnostic run: **15 passed** in the final run, including actual FK/NOT NULL failure rollback during dispatch/reconciliation, query-only transaction enforcement, multiple-order rejection and a two-LKR-350 scenario reproducing zero completed sales / LKR 700 cash contribution. Browser cancellation sends no reconciliation POST; confirmed local mock recovery posts once without resending CREATE.
- Python compile, JavaScript syntax and whitespace checks passed.
- Broad POS/accounting/receipt/browser, hardware/agent/ingress, Retail/scanner, inventory, Shopify/payment/fulfillment, staff and WhatsApp regression: **510 passed, 2 skipped, 0 failed**. Both PostgreSQL-engine-only skips passed separately on disposable PostgreSQL (**1 Draft Order concurrency + 1 fulfillment concurrency**). Runs overlap; do not sum as unique coverage. All databases are disposable; Shopify/hardware calls are mocked.

[Findings, exact authorized read-only command and recovery procedure](pos-sale-readback-recovery.md). Production order verification and the exact old failure remain explicit blockers; tests do not certify live Shopify/DB state or authorize financial recovery.
