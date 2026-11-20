# POS CREATE/read-back incident — 20 November 2026

**Updated production evidence:** See [full-read and reference diagnostic](pos-order-read-transport-diagnostic.md). Sale B was not found in the configured shop; the earlier assumption that A releases B is unverified. Do not use the conditional recovery steps below as authorization. Current A failure is at full Shopify READ, before order verification/local posting.


## Evidence and conclusion

Inspected deployed-compatible main **0fd48c3**, merged Phase 4G PR #183. Live observations are supplied by the owner; this development environment has no authorized Railway/Shopify connector or Railway CLI. No live credentials were requested/copied and no live API verification, financial mutation, reconciliation, print or variable change was performed.

| Sale | Owner evidence | Code-confirmed interpretation | Still to verify live |
| --- | --- | --- | --- |
| POS-851C4716B015DF1B0E79 | REVIEW; CREATE; READBACK_OR_POSTING_UNAVAILABLE; LKR 350 cash VERIFIED; **owner confirms the corresponding order is visible in Shopify under this reference** | CREATE returned an order ID. The subsequent read/verification/local-posting savepoint failed. The operation is REVIEW and blocks subsequent jobs for the same shop. **Do not resend CREATE.** | Recorded remote ID, current authenticated order read/search and the exact validation/posting failure. |
| POS-5AB126C311C38CDBD5FA | QUEUED; LKR 350 cash VERIFIED | Sale A's REVIEW operation is sufficient to block this shop's queue. The badge alone does not establish that B was never attempted. | B's operation status/attempt count/outcome ID and exact-reference search, plus any other shop blockers. |

The successful CREATE return and the owner’s subsequent confirmation establish that a corresponding order is present in Shopify. Its current paid/fulfilled/uncancelled state and complete correlation still require authenticated read-only API verification; no order GID or field-level API result was supplied here. The old generic failure audit discarded the exception/stage, so a new diagnostic cannot reconstruct the exact historical exception. It checks the **current** remote/local evidence; a transient failure may no longer reproduce.

Expected register cash includes opening float and accepted cash movements, which are created when the tender is accepted. The reported LKR 700 is consistent with two accepted LKR 350 tenders; it is not proof that two sales completed or that cash was physically counted. Sales History's verified total excludes REVIEW/QUEUED, so zero is consistent. Final publication posts local accounting but does not record another cash receipt/movement.

## Conditions behind READBACK_OR_POSTING_UNAVAILABLE

The current main code places these under one catch after CREATE returns:

1. **Shopify read transport/query:** timeout, network/token/access problem, non-200 status, GraphQL errors, unexpected API version/shape or unavailable order.
2. **Identity:** valid order ID, matching sourceIdentifier and POS tag, uncancelled order, correct customer association.
3. **Financial verification:** LKR currency, exact frozen total/received/tax, PAID status, no refund and no unexpected Shopify-native discount; successful manual SALE/CAPTURE transactions with matching received amounts and complete transaction coverage.
4. **Lines:** complete line coverage, unique line/segment correlation attributes, original variant/quantity and exact net prices matching the frozen allocation.
5. **Fulfillment:** expected single successful store fulfillment, configured location and complete quantities/line coverage.
6. **Local posting/persistence:** unbalanced frozen entries, absent/invalid account references, SQL/constraint/immutability/connection failures, payment linkage/order update failure or other code exceptions during posting/flush.

The original savepoint rolls back partial local publication and retains `outcome.remote_id` / REMOTE_RETURNED evidence. An outer commit failure would normally leave a different interrupted state rather than a durably recorded REVIEW code.

Cash verification or Shopify preflight authorization failure before CREATE would normally produce another state/code, not this error. The worker demonstrably reached the post-CREATE stage for A; checkout writes being false is not the explanation for A. They are owner-confirmed true. The queue fence is an intentional safety mechanism, not a reason to bypass it.

Completion uses synchronous authenticated read-back, not a webhook callback. Shopify read-after-write/search/fulfillment consistency could cause validation to fail, but **webhook delay is not an established root cause** and no webhook change is justified. No strict verification criterion is relaxed in this fix.

## Authenticated read-only inspection — first action

New command:

```sh
python -m scripts.inspect_pos_sales_readonly \
  --reference POS-851C4716B015DF1B0E79 \
  --reference POS-5AB126C311C38CDBD5FA
```

Run only inside the **existing authorized Railway runtime**, using its existing Shopify connection. This is not executable against production from Codex here. It performs only SELECTs in a read-only database transaction and allowlisted Shopify READ/FIND queries. It does not import/start the web worker, dispatch, reconcile, create/refund, generate a receipt or alter stock. OAuth token acquisition/refresh uses the existing client inside the runtime; no secret is printed. No raw order/customer/payment/SQL/exception payload is exported.

It reports current operation/queue blockers, returned order ID, exact-reference search results, full read-back verification against the frozen sale, safe reason codes, existing posting/payment-link counts, balance/account/hash checks and session/policy readiness. A direct read of the recorded ID is attempted even if Shopify search has not indexed the tag yet. The existing reconciliation **still requires exactly one search match**; zero/multiple matches never authorize resend. Read-only checks cannot prove a future DB write will succeed or that external data will not change; the approved reconciliation re-reads/re-verifies.

If code is not deployed, an authorized administrator may instead review/copy **only** `scripts/inspect_pos_sales_readonly.py` and `pos_failure_codes.py` into a temporary directory such as `/tmp/pos-incident-ro`, retaining their reviewed PR contents. From the current deployed repository directory:

```sh
PYTHONPATH="$PWD:/tmp/pos-incident-ro" python /tmp/pos-incident-ro/inspect_pos_sales_readonly.py \
  --reference POS-851C4716B015DF1B0E79 \
  --reference POS-5AB126C311C38CDBD5FA
```

This is a command-local import path, not a Railway/application configuration change. It can use the existing main version of pos_service/pos_shopify; no application deployment/restart is required for the probe. Do not copy credentials, dump environment values, call dispatch_one or use a new app/store. Review the probe first. Results are an observation, never write authorization. No live run is claimed here.

## Is the existing reconciliation button safe?

**It does not resend Shopify CREATE/refund and does not perform another inventory adjustment. It is not read-only for the CRM database.** It requires administrator authorization and evidence notes, searches/reads and strictly verifies the existing order, then can:

- Create the canonical POS journal posting (once per sale posting reference).
- Link existing payment records to it, without creating another payment/cash movement.
- Link/update the local commercial order, set the checkout COMPLETE and operation VERIFIED, and append audit.
- Unblock the worker, which can then dispatch **Sale B automatically** because checkout writes are enabled.

It uses the existing local posting idempotency/fences; calling it again after verification is rejected. The journal date follows the existing current Sri Lanka posting date policy, so a cross-date recovery needs accounting-period review.

The main-branch label “Read-only reconciliation (no resend)” is misleading because “read-only” applies only to Shopify. The fix renames it to **Verify Shopify & finalize locally (no resend)**, shows safe diagnostics and requires an explicit confirmation about local accounting. Failed reconciliation is now enclosed in a savepoint, retains the original blocker/attempt fence, records only safe diagnostic metadata and returns a useful 409 instead of an opaque error. These new local safeguards do not authorize executing it against production during inspection.

## Exact safe recovery sequence

1. **Stop initiating new counter checkouts for troubleshooting.** Retain both references and the already verified payments. Do not collect another LKR 350, create replacement carts/orders, change frozen amounts, delete payments, reset attempts, force COMPLETE or manually increment/decrement stock.
2. Run the read-only probe above. Confirm source shop identity through the existing connection and inspect both operations, including other blockers. A's returned order ID must be read and verified. A matching amount alone is not order identity.
3. Check A's current order correlation, exact money/tax/payment/line/fulfillment/location evidence. If mismatched, refunded, cancelled, missing, ambiguous or unavailable: **stop**. Review the indicated domain with the administrator. Do not resend CREATE or relax verification to make it pass.
4. If search has zero matches but the recorded order GET verifies, treat search/index consistency as unresolved. Repeat read-only checks later; the current button deliberately cannot finalize from an ambiguous search. Multiple matches require explicit incident review, not selecting one silently.
5. Inspect local posting metadata: valid frozen hash, existing expected accounts, balanced entries, expected verified tenders and no unexplained orphan/mismatching journal/payment linkage. No dry-run INSERTs against production are performed by the probe. Resolve a proven schema/constraint/configuration defect through a reviewed fix; do not rewrite history.
6. **Obtain explicit approval for local recovery and the possible release of B.** Before reconciling A, inspect B's attempt/outcome/search. If B has been attempted or has a remote order, do not allow another CREATE. If B is genuinely unattempted/queued with no matching remote order, normal processing after A is eligible only under the approved recovery plan. If only A is approved, arrange administrator-approved worker quiescence/maintenance first; do not invent a pause variable or change Railway flags without approval.
7. On the existing A operation only, use the authorized reconciliation button with factual evidence notes. It re-reads the order and posts locally without resending CREATE. If it fails, retain REVIEW/UNCERTAIN and inspect the returned diagnostic; do not reset the operation. Verify exactly one local POSPosting and correct payment linkage after success.
8. Under the approved plan, let the normal worker process a genuinely unattempted B once, using fresh identity/price/stock checks. Do not call CREATE directly. If B has its own uncertainty, use the same read-only inspection/reconciliation decision process.
9. Verify A/B show COMPLETE only after evidence passes. Within the matching Sales History date/filter, expected verified sales become LKR 350 then LKR 700; expected register cash should not gain another LKR 700 from completion. Check the existing cash movement/payment counts and actual physical cash separately.
10. Only after completion and separate physical-print approval, use **Print Receipt (80 mm)** through Chrome/POSPrinter POS-80, actual size, one copy. Printing remains downstream. Windows driver cashbox settings may independently open the drawer; agent ingress/automation stays disabled.

## Required fixes / release boundaries

This PR fixes verified observability/recovery/UI defects, not an unverified Shopify mismatch:

- Allowlisted stage/reason metadata for post-CREATE and reconciliation failures, preserving the existing top-level error and remote ID. No exception, SQL, token or raw payload logging is added.
- Explicit reconciliation savepoint/failure response and honest local-accounting confirmation. No CREATE resend path, new payment, stock adjustment or relaxed verification.
- Interrupted dispatch updates a formerly QUEUED sale badge to UNCERTAIN while retaining attempts=1 and the global safety block.
- A read-only authenticated incident probe and regression tests for A blocking B, transient read-back, strict mismatches, PostgreSQL posting rollback, single recovery and no duplicate payment/order/stock mutation.

No migration, dependency change or new Railway variable. No global queue fence removal or refund/checkout auto-enablement. Source financial calculations and Shopify mutation wire are unchanged. The precise live failure behind A's old generic error remains missing evidence until the authorized probe/log review is done.

**No Railway configuration change is currently justified.** Keep the confirmed checkout setting as authorized, do not toggle it to “retry”, and leave advanced ingress/drawer flags disabled. If evidence later establishes a real connection/access/configuration failure, address only that verified issue with separate approval; do not change the global Shopify API/webhook version as a guess.

After review, deploy through the normal approved process and reload the cashier web app for the updated label/diagnostics. This task does not merge or deploy automatically. Rollback uses a reviewed code revert; preserve all existing audits, postings, remote IDs and attempt fences. A code rollback does not undo an approved accounting recovery; do not delete financial records or recreate orders to roll it back.
