# PR #184 follow-up: full order read and missing reference

## Current evidence (20 November 2026)

The operator reports deployment `432e896075b087d3750951e12b8458ae0ceda9cb`.
Sale A `POS-851C4716B015DF1B0E79` has one CREATE attempt, recorded order
`gid://shopify/Order/7610874233135`, one search match, and
`SHOPIFY_READBACK_UNAVAILABLE`. The diagnostic reached the full Shopify READ
and it raised before canonical order verification or local posting. Search uses
a smaller query. This establishes a current full-read failure, not its underlying
HTTP/GraphQL/transport cause. The old generic historical exception cannot be recovered.

`existing_reconciliation_search_condition_met` requires the unique search match,
verified complete read-back, and matching order identity. One search result alone
cannot satisfy it. Existing accounts, valid frozen hash and accepted cash do not
verify remote payment, lines or fulfillment. No such live field verification is
claimed here.

Sale B `POS-5AB126C311C38CDBD5FA` was not found by the original exact-reference
AND configured-shop lookup. That does not establish deletion or another shop.
The new probe searches that exact reference across shop contexts in the current
schema, reports bounded normalized-reference candidates, and optionally looks up
the UUID from a fresh desktop checkout GET. It never changes references or tenant
filters in the application. A database-context fingerprint helps distinguish
runtime/database/schema differences without printing connection credentials.

A blocks its configured shop. **It is not established that completing A releases
B.** If B belongs to another shop, A does not unblock that shop; if absent from
this runtime/schema, no B operation can be inferred. Do not recreate B or collect
cash again. Compare the fresh desktop's origin, checkout UUID and reference with
the authorized runtime. Do not export cookies, headers or full checkout responses.

## Authorized operator procedure: no financial writes

Codex has no authenticated production runtime access. These commands must be run
by an authorized operator inside the existing Railway runtime, from the deployed
repository directory. Do not dump environment variables or add credentials.
Review the script first. It uses the existing token provider and fixed Shopify
queries only, and a database read-only transaction; it does not start the worker.
Token acquisition uses the existing OAuth connection. No order/customer/payment
payload, raw exception, SQL or GraphQL error message is printed.

Once the reviewed script is available:

```sh
python -B -m scripts.diagnose_pos_order_readonly \
  --order-id gid://shopify/Order/7610874233135 --groups full
```

No deployment is required merely to inspect: an administrator may copy only the
reviewed `scripts/diagnose_pos_order_readonly.py` to
`/tmp/diagnose_pos_order_readonly.py`. With PR #184's existing modules present:

```sh
PYTHONPATH="$PWD" python -B /tmp/diagnose_pos_order_readonly.py \
  --order-id gid://shopify/Order/7610874233135 --groups full
```

This command-local import path does not change Railway variables. Default is one
full read. If it fails, isolate the field group with this bounded seven-query run:

```sh
PYTHONPATH="$PWD" python -B /tmp/diagnose_pos_order_readonly.py \
  --order-id gid://shopify/Order/7610874233135 \
  --groups identity,totals,customer,lines,transactions,fulfillment,refunds
```

The probe stops further reads on HTTP 429 or GraphQL THROTTLED. Share sanitized
JSON only. Optional `--checkout-id UUID` locates B using the current desktop's
internal checkout ID. Missing B still means only this database/schema was searched.

Interpret `http_status`, returned API version, GraphQL code/path/field/type and
query cost. ACCESS_DENIED, unavailable schema fields, cost limits, transport
failures and wrong API/shop identity require different fixes. Do not guess which
occurred. Partial group successes never authorize reconciliation and are never
assembled into an order verification result. Only the unchanged complete READ
is passed to the unchanged canonical verifier. Stop on recorded-order mismatch.

## Recovery boundary

No reconciliation is authorized by this investigation. Do not retry CREATE,
reset attempts, force COMPLETE, edit payments/journals, or adjust inventory.
Once the actual read error is proven, fix only its verified cause with reviewed
regression tests. Do not remove verification fields or enable refund writes to
make READ pass. No Railway configuration change is currently justified.

After the complete read works, rerun the existing PR #184 probe:

```sh
python -B -m scripts.inspect_pos_sales_readonly \
  --reference POS-851C4716B015DF1B0E79 \
  --reference POS-5AB126C311C38CDBD5FA
```

Require matching recorded order, unique search, full canonical identity/payment/
line/fulfillment/location verification, and consistent local posting/payment
state. Locate B and inspect its attempts and remote evidence first. A recovery
can release other same-shop jobs with checkout writes enabled. Arrange separately
approved worker quiescence if only A is approved; do not invent a pause variable.

Only after explicit approval use A's existing **Verify Shopify & finalize locally
(no resend)** action. It reads Shopify but writes local accounting/status/audit;
it is not a read-only button. It must not resend CREATE or adjust Shopify stock.
Existing idempotency protects local publication; no successful recovery is claimed.
Keep B stopped until its identity, attempts and duplicate-order risk are resolved.
Receipt printing follows verified completion and separate printing approval.

## Code/release scope

Only diagnostic script, isolated tests and documentation change. No migration,
new setting, production adapter, import, Draft Order, webhook, checkout worker,
financial calculation or verification rule changes. No automatic deploy/merge.
Rollback removes this diagnostic addition; it does not change production records.

## New Shopify Admin / CRM Sales Orders evidence

The owner's screenshots describe Shopify #1806 as PAID/FULFILLED for one Halloween
Party Shaker Pen, LKR 350, at Magammana West. This is consistent with CREATE having
succeeded. It is not the full canonical API evidence: notes and displayed channel
alone do not prove `sourceIdentifier`, the POS tag, variant/segment properties,
exact money, transaction evidence or fulfillment line coverage. The app channel
label “PixiePinks WhatsApp AI” is not a POS verifier condition; the existing app
connection can attribute POS-created orders to that channel.

CRM Sales Orders reads `sales_orders` by integer ID and internal order number
without the POS shop predicate. POS Sales History reads `pos_checkouts` by UUID,
reference and configured shop, linked through `sales_order_id`. Normal checkout
creates both in the same transaction. Accepted POS tenders can mark the commercial
payment PAID while order_status remains PAYMENT_VERIFICATION. Shopify linkage,
local fulfillment FULFILLED and DELIVERED are written only by `publish_sale`
after strict remote verification and journal posting. Therefore A's stale CRM
statuses are consistent with the failed full read; they do not justify manually
linking or marking fulfilled.

The probe now includes `crm_sales_orders` for each exact internal order number,
with commercial status/link metadata and linked checkouts across stored shops.
It exports no customer details, tender payloads or item data. If B's commercial
record exists without a checkout, retain the record/payment and investigate the
creation/import/restore history; do not synthesize a new checkout. If its linked
checkout has another reference/shop, report the mismatch without rewriting it.
If both lookups are absent, compare the screenshot's current origin/database
context, integer SalesOrder ID and exact reference; a screenshot may show a
record from a different runtime or earlier state. None of these is established
until the operator runs the probe.

Fulfillment matters: the canonical verifier requires exactly one returned
fulfillment (a result of two or more is rejected), status SUCCESS, the frozen
location GID and complete matching line quantities. The displayed FULFILLED badge
alone is insufficient. It does not currently gate on `displayFulfillmentStatus`;
it verifies the underlying fulfillment records. No second fulfillment or stock
adjustment is needed or authorized merely because CRM says UNFULFILLED.

Before recovery require A's recorded ID and exactly one correlated order;
canonical verification of all frozen payment/line/customer/tax/location evidence;
no unexplained posting/payment linkage; and B's actual checkout/attempt/remote
identity. Existing duplicate-checkout, business-key, queue fencing and posting
idempotency protections remain intact, but B's live missing checkout is not
resolved by those protections. Never resend A CREATE or recreate B based on its
commercial badge. The smallest current change is diagnostic coverage; an API
adapter fix must follow the sanitized real failure, not screenshot inference.
