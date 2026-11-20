# Retire the two owner-confirmed POS test transactions

## Authorization boundary

Prepared only. No production cleanup, refund, restock, reconciliation, CREATE,
printing or Railway variable change has been executed. Owner confirmation that
these were simulated test purchases is recorded in this incident's request;
execution still requires explicit approval of the actual dry-run manifest.

Scope is hard-bound to shop `eb2b70`, references
`POS-851C4716B015DF1B0E79` and `POS-5AB126C311C38CDBD5FA`, LKR350 each,
and A's recorded order `gid://shopify/Order/7610874233135`.
No generic test flag permits cancelling other transactions. Existing normal void,
refund, reconciliation, worker and verification rules remain unchanged.

The normal unsent void refuses A's attempted CREATE and records physical cash
returned. Do not bypass it, use a refund, or falsely attest a cash return.
The dedicated command appends TEST_TENDER_CORRECTION movements for simulated
cash, preserving the original SALE_TENDER records and all evidence.

## First identify records, without modifying them

This Codex environment has no authorized production Railway connection. The
operator must review and make the new code available in the existing runtime.
Use approved code delivery, not credentials in chat. Both `pos_test_cleanup.py`
and `scripts/retire_pos_test_sales.py` are required. The earlier extended
`scripts/diagnose_pos_order_readonly.py` can first trace missing B through its
commercial order. From the existing deployed repository directory:

```sh
python -B -m scripts.diagnose_pos_order_readonly \
  --order-id gid://shopify/Order/7610874233135 --groups full
```

If deploying code is not yet approved, an operator may review/copy
`pos_test_cleanup.py` and `retire_pos_test_sales.py` into
`/tmp/pos-test-retirement`, and the diagnostic into that directory, then execute
from the deployed repository directory:

```sh
PYTHONPATH="/tmp/pos-test-retirement:$PWD" python -B /tmp/pos-test-retirement/diagnose_pos_order_readonly.py \
  --order-id gid://shopify/Order/7610874233135 --groups full
```

A's full-read failure must be resolved or otherwise separately reviewed before
this deliberately strict retirement path will succeed. Screenshots do not replace
API identity/payment/location/quantity verification. Missing or other-shop B is a
blocker: inspect SalesOrder ID, linked checkout UUID/shop/reference and deployment
DB/schema; do not manufacture a replacement record or expand the hard-bound scope.

## Dry-run manifest — read-only database transaction

Use the ID of an existing active administrator with POS administration permission.
No new account or credentials are required. Substitute the numeric ID for ADMIN_ID:

```sh
python -B -m scripts.retire_pos_test_sales --admin-id ADMIN_ID
```

Or, for reviewed temporary files only:

```sh
PYTHONPATH="/tmp/pos-test-retirement:$PWD" python -B /tmp/pos-test-retirement/retire_pos_test_sales.py --admin-id ADMIN_ID
```

Default mode uses a DB read-only transaction. Shopify access is restricted to
READ/FIND by the existing ReadOnlyClient. Token acquisition uses the existing
connection; no token is printed. The command does not import/start the worker.
It is not an API endpoint and supplies no ambient write authorization.

It requires:

- Exactly one matching checkout for each reference in the configured approved
  shop, linked to its exact POS SalesOrder and valid immutable frozen snapshot.
- A in REVIEW with exactly one CREATE attempt, its unique source-correlated
  Shopify search match, the expected remote ID and a complete canonical read.
- B QUEUED, zero attempts, no stored remote outcome/link and no source-correlated
  Shopify search match. Incomplete/failed searches are blockers. A tag/source
  search cannot prove absence of orders whose correlation was externally removed;
  review Shopify Admin and pause competing manual/integration editors. Any
  contrary order evidence requires separate review, never a new CREATE.
- One verified POS CASH tender of LKR350 per sale; no accounting linkage, refund,
  POSPosting or canonical journal memo. An existing journal requires a separately
  reviewed reversal; this tool never deletes/reverses posted journals.
- Exactly the original matching SALE_TENDER movement, no prior test correction,
  an OPEN matching register session, and no DISPATCHING job in the shop.

The successful JSON identifies exact checkout, commercial order, operation,
payment, register session and original cash-movement IDs; attempts and remote
references; per-session cash totals before/after; other queued jobs/blockers;
and a `plan_sha256`. A blocked run is not a successful manifest. Do not turn a
blocker into an approval by editing database rows or constants.

Expected changes, **conditional on live verification**, are:

| Records | Proposed change |
| --- | --- |
| Both POSCheckout rows | CANCELLED |
| Both CREATE operations | CANCELLED; preserve attempts, wire, outcome IDs, errors |
| Both SalesOrders | CANCELLED, derived amount_paid 0 and payment UNPAID |
| Two original SalesPayment rows | VERIFIED → REJECTED, explicit simulated-test reason; preserve amounts |
| Register ledger | Append −LKR350 per sale; no physical refund claim |
| Audit | Append payment decision and TEST_DATA_RETIRED records with manifest hash |
| Journals / inventory / Shopify / genuine sales | No changes |

Combined expected-cash reduction is LKR700, not necessarily a final drawer total
of zero. Opening float and genuine cash movements remain. Different sessions
receive their own corrections. Closed sessions fail rather than rewrite a signed
off closing report. Correction is local only: Shopify #1806 remains unchanged
and may still appear in Shopify totals until separate cleanup is authorized.

## Separately approved execution — do not run as part of investigation

Present the complete dry run to the owner. Approval must name both exact test
references, −LKR700 simulated cash correction, the manifest hash and permission
to release other queued normal work shown in the report. Stop taking new sales
during the short maintenance window. Pause competing Shopify editors/integrations
through an administrator-approved operational procedure; do not change Railway
flags or create a new pause variable. A full shop reset is unnecessary.

Only after explicit approval, an authorized administrator may run:

```sh
python -B -m scripts.retire_pos_test_sales \
  --admin-id ADMIN_ID \
  --apply-approved-plan APPROVED_PLAN_SHA256 \
  --confirm 'CANCEL TEST DATA POS-851C4716B015DF1B0E79 POS-5AB126C311C38CDBD5FA; NO SHOPIFY CLEANUP' \
  --notes 'Owner approved both simulated test tenders and the exact dry-run manifest; no physical cash refund or Shopify cleanup'
```

Temporary-file execution uses the same reviewed import path as above. Do not
paste a hash from a different run. Apply requires PostgreSQL and active admin
permission. It acquires the existing shop worker lock, checks no in-flight job,
repeats all checks and remote reads, and requires the fresh plan hash to match.
Both sales are cancelled atomically before the lock is released. There is no
CREATE, refund, fulfillment or stock mutation path. Any failure rolls back the
local transaction; no automatic retry. Independent Shopify edits remain outside
the database lock, so there is no claim of cross-system atomicity.

## Verify after authorized execution

1. Inspect both exact checkouts/operations: CANCELLED, original attempts/outcome
   IDs retained, matching SalesOrders cancelled, original payments retained but
   REJECTED with test reasons, and two new correction/audit records.
2. Check expected cash fell exactly LKR700 across the affected sessions. Verify
   the two original movement records remain and no duplicate correction exists.
3. Verify canonical journal/posting counts remain unchanged and no Shopify,
   inventory, other commercial orders or genuine payment records changed.
4. Re-run `scripts.inspect_pos_sales_readonly` for both references. Confirm no
   active queue blockers from these two operations. It will still show existing
   remote A; that is expected. Other unrelated blockers must be reviewed, not
   bypassed. A missing B is still a stop condition, never permission to recreate.
5. Only then accept one separately authorized normal sale. Normal worker
   verification/accounting controls still apply; do not automatically print.

Rerunning apply after success fails on terminal state rather than adding another
cash correction. If commit outcome is uncertain, inspect audits/status/correction
references first; never blindly retry. Do not roll back cleanup by deleting cash,
payment or audit rows. Any correction to an executed retirement needs a separate
approved append-only accounting/register procedure. Reverting code does not
reverse records. No migration or new environment variable is required.

## Railway code deployment (separate approval)

1. Review the PR, tests and fixed constants before merging. Feature-branch
   publication alone does not authorize deployment or cleanup.
2. Through the administrator's normal approved main/release workflow, deploy the
   reviewed commit. If Railway auto-deploys main, approval to merge must account
   for that behavior; this task neither merges nor triggers deployment.
3. Verify the deployed commit matches the approved Git SHA. No migration,
   dependency installation, start-command change or new environment variable is
   required. Do not add this utility to Procfile, startup hooks, cron or a worker.
4. Keep checkout/refund/printing/ingress/barcode flags at their existing values.
   Deployment adds a manual utility only; it performs no cleanup on startup.
5. In the authenticated runtime, run the read-only discovery and default manifest
   commands above. Review live blockers and obtain the owner's separate approval
   of that exact manifest/hash before any application command.
6. If deployment must be rolled back before execution, restore the previous
   reviewed code release. No database rollback is needed for code-only deployment.
   After any approved cleanup, retain its immutable audit/corrections; rollback
   of code must not delete or reconstruct transaction history.

## Regression verification before publication

Run against disposable local databases and mocked Shopify only:

```sh
python -m pytest -q tests/test_pos_test_cleanup.py tests/test_pos.py \
  tests/test_pos_adapter.py tests/test_pos_incident_diagnostics.py \
  tests/test_pos_read_transport_diagnostic.py tests/test_finance_reporting.py \
  tests/test_finance_reporting_pos.py tests/test_pos_receipts_phase4g.py \
  tests/test_pos_reconcile_ui.py
python -m pytest -q tests/test_pos_browser.py tests/test_pos_receipt_browser_phase4g.py
```

With a dedicated local `TEST_POSTGRESQL_URL` accepted by the existing PG fixture:

```sh
python -m pytest -q tests/test_pos_test_cleanup_postgres.py \
  tests/test_pos_postgres.py tests/test_pos_incident_postgres.py \
  tests/test_finance_reporting_pos.py::test_postgres_financial_aggregation_uses_decimal_and_inclusive_dates
```

Coverage includes exact production constants, default CLI read-only mode,
repeated/stale/unauthorized execution rejection, missing or attempted B, B remote
order detection, A read failure, closed sessions, existing posting rejection,
transaction rollback, retention of a completed unrelated sale and its accounting,
normal checkout after retirement, and PostgreSQL exclusion of the worker while
both test operations are cancelled. Browser tests perform no physical printing.
