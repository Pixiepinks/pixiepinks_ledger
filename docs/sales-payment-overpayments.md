# CRM overpayment recording

For an order of LKR 22,410 and an independently verified bank receipt of LKR
23,000, record **23,000 received**, **22,410 applied**, and **590 held for review**.
The order total never changes. Existing `SalesPayment.amount` continues to mean
order-applied money; nullable `received_amount` records actual claimed/verified
receipt. Legacy NULL values mean received equals applied. Pending claims reserve
only their applied amount under the existing order-row lock.

## Staff workflow

1. Open Sales Orders → the relevant WhatsApp order → Record payment claim.
2. Enter the actual amount received, date, payment method and transaction reference.
3. If it exceeds the unreserved balance, select the explicit retain-excess checkbox
   and enter a reason, such as exchange-rate difference. Do not enter an exchange
   rate to change the order total. Amounts are in the order's currency, not a
   foreign-currency amount or an assumed bank conversion.
4. Record the pending claim. The history shows applied, received and excess values.
5. An authorised verifier independently checks the actual funds and verifies or
   rejects the claim using the existing permissions. Only verified excess becomes
   HELD FOR REVIEW; a pending/rejected claim is not evidence of received funds.
6. Agree the excess disposition with the customer and administrator. This feature
   **does not create spendable customer credit, refund money or post accounting**.
   Keep the excess on the order for review; do not record it again as a second
   payment, discount, revenue or loyalty purchase. Use a separately reviewed
   accounting/refund/customer-credit process before returning or applying funds.

There is deliberately no arbitrary “clear excess” button: hiding an unresolved
liability without a corresponding approved financial disposition is unsafe.
Unidentified legacy orders retain evidence against their order ID; no customer is
created, matched or credited by name/phone. POS tenders/change retain their strict
existing behavior and cannot opt into this workflow.

## Integrity and integration

The existing idempotency key binds the excess opt-in/reason as well as original
claim input. Legacy request hashes are preserved when new fields are absent.
Transaction-reference deduplication, pending reservations, order locking and
verification limits remain intact. The API still rejects overpayment by default.
Only explicit JSON boolean `retain_excess: true` plus a reason allows the split.
No balance remaining means staff must review existing claims; this is not a
standalone deposits/customer-credit entry system.

Existing payment/decision audit events retain the received/applied/excess amounts
and reason in their evidence. UI/API statuses derive from the verified claim; no
new background worker, external request or accounting event is introduced.
Shopify payment sync and loyalty qualification continue to aggregate only the
applied amount. No Shopify API permissions or live settings change.

## Deployment (approval required)

- Back up the database and deploy the reviewed commit through the normal release
  process. Migration **20261126_60**, parent **20261125_59**, adds two nullable
  payment columns. It preserves all amounts and existing payment rows.
- Run `python -m alembic current` and confirm revision 60 using the project's
  normal migration/startup procedure. No new environment variable is required.
- Retain existing payment-tracking/Shopify write gates and permissions; do not
  enable any settings merely to deploy this feature. Preserve loyalty ON state.
- In a disposable environment, check partial, full, excess and rejected claims,
  pending reservations, reference deduplication and authorised verification.
- On desktop/Android verify that history displays received/applied/excess clearly,
  and that the verifier can see those figures before confirming actual funds.
- No production payment or refund is part of deployment acceptance.

## Rollback

Disable access to new excess entry while investigating; preserve all evidence.
Use a compatible application rollback that retains revision 60, its model fields
and the excess history/API, while reverting the new-entry controls. A build that
cannot resolve revision 60 cannot run the normal Alembic startup safely. Older UI
also hides excess, so do not use an unmodified old build to manage these receipts. Do not downgrade the schema if any received
amount differs from applied amount, including rejected claims: downgrade refuses
before DDL to avoid erasing evidence. A clean disposable database without excess
can downgrade to revision 59. Never delete payments to make downgrade succeed.

## Scope limitation

The current CRM payment module is operational verification, not a posting engine.
This delivery safely records the full receipt and unresolved excess; reusable
customer credit, liability journal posting and refund execution require their own
approved, idempotent financial workflow and are not silently simulated here.
