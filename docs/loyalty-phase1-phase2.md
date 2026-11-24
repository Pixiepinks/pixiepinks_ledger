# PixiePinks Loyalty Club — Phases 1 and 2

Baseline: `2c1457ffadcba8cd1f8469eec8801fe2bc7486e4` (main, including manual unread PR #206).
Branch: `feature/loyalty-phase1-phase2`. Migration: `20261124_55`, parent `20261123_54`.

## Release status

Implementation is local CRM loyalty pricing and management, **not a points scheme**. The database programme switch defaults **OFF**. Deploying or migrating does not turn discounts on. No Shopify product, inventory, scope or authentication changes, POS pricing hooks, new messaging, automatic printing or new environment variables are introduced.

Production activation requires administrator approval, review of historical coverage and customer links, verified cost inputs and the acceptance checklist below. No production runtime or full Shopify order history was accessible during development. Automated tests use disposable databases and mocked Shopify reads. Screenshots use synthetic records. Android physical-device and live Shopify tests remain operator checks.

## Architecture and data ownership

Existing `Customer.id` is the authoritative identity; a phone is never a merge key. Existing `SalesOrder`, verified `SalesPayment`, imported Shopify payment snapshots and completed POS checkout/refund evidence provide purchase history. Shopify remains the product catalogue and image source through the existing `RetailVariant` import and live CRM product resolver.

The additive migration creates fourteen tables:

- `loyalty_config`: programme state, minimum net purchase, minimum margin and safe pricing policies.
- `loyalty_members`: unique customer association, sequential `PP-L-000001` identity, status, activation, cached history/savings, management notes and optional unique Shopify customer GID.
- `loyalty_rules` / `loyalty_rule_versions`: variant/channel/time-bound offers and immutable versions.
- `loyalty_order_pricing`: draft and frozen complete pricing snapshots with SHA-256 digest.
- `loyalty_redemptions` / `loyalty_redemption_events`: one grant per order line, immutable pending/finalized/reversal/release events.
- `loyalty_audits`: append-only actions, actors, reasons and before/after state.
- `loyalty_identity_reviews` / `loyalty_order_identities`: explicit review and immutable loyalty attribution of previously unlinked orders; no customer merges or reassignment of authoritative order links.
- `loyalty_migrations` / `loyalty_migration_rows`: reviewable local-history manifests and resumable execution.
- `loyalty_recalculations`: transactional, customer-keyed local outbox.
- `loyalty_order_exclusions`: immutable management designation of noncommercial test history for loyalty only.

See migration source for exact constraints/indexes. PostgreSQL and SQLite triggers prevent rewriting/deleting audit events, rule versions, redemptions, identity mappings and test exclusions. Frozen pricing cannot be updated or deleted. Unique constraints protect customer membership, rule versions, line redemptions and event identities. PostgreSQL row locks serialize conflicting rule changes, membership creation, approval execution and confirmation. Membership sequence gaps are permitted; identifiers are never recycled.

`loyalty_events.py` collects existing customer/order/payment changes in the same transaction and upserts the local outbox. The existing Retail worker processes it; it does not call Shopify or post financial entries. Five failed recalculations become REVIEW with a fixed sanitized error code. Management can audit a retry. Eligibility is computed from current verified history, so stale cached member totals cannot authorize discounts. Reports flag mismatched refund/payment evidence as NEEDS_REVIEW rather than including it as verified performance.

## Qualifying purchases and identities

A qualifying purchase must have a positive LKR total, be operationally completed (`DELIVERED`, or a POS checkout actually `COMPLETE`), and be fully paid with verified receipts or matching imported Shopify payment evidence. Merely entering an amount, an unpaid COD promise, a draft, a cancelled/returned order or a pending POS operation never qualifies. Known historical test references, explicitly designated test history, test channels/keys and synthetic POS snapshots are excluded.

Cumulative refunds reduce qualifying net paid. Full refunds remove that purchase from eligibility; an independent qualifying purchase can keep membership active. POS refund evidence and imported Shopify aggregate refunds are reconciled using the greater known cumulative total to avoid counting the same refund twice. Missing or invalid refund evidence fails closed. The current order is excluded from its own eligibility: a first purchase cannot discount itself.

Last-purchase time uses audited delivery/completion timestamps. Missing historical completion time is shown unavailable rather than invented from creation time. Migration range filters use order creation dates explicitly.

Phone normalization reuses `marketing_identity.phone`, including Sri Lankan local/international and supported overseas forms. A valid mobile is required. Shared normalized mobiles remain distinct Customer IDs and are held for management identity attestation. The CRM editor requires explicit Customer selection and consistent registered mobile before applying loyalty; it does not silently link a WhatsApp sender. Management can suspend/reinstate, add notes, verify shared identity or record a unique Shopify customer GID. These actions require reasons and retain audit history.

## Pricing rules and confirmation

Supported methods: percentage off, fixed amount off, explicit loyalty unit price. Offers are **CRM only**, with half-open validity intervals `[start, end)`, per-order eligible quantity limits and no overlapping enabled rule for a variant/channel. Duplicate cart lines share the quantity cap. All money uses Decimal, two-decimal half-up rounding; quantities use existing three-decimal limits.

Reliable costs are not supplied by the existing retail catalogue. An authorized manager must attest the unit cost with source reference and verification date. Unknown cost is not zero. Variable cost is optional; unknown contribution is displayed unavailable. Default minimum gross margin is 20%. Missing cost, below-cost prices, negative known contribution and insufficient margins prevent activation/application unless an independently authorized profitability exception with meaningful reason is recorded. Future/invalid costs and invalid selling prices are rejected. Existing rule projections recheck current catalogue state and costs.

Public promotions and loyalty never stack. Existing line/order discounts are allocated and compared with loyalty; the lower payable amount wins, with public promotions winning ties. A Shopify compare-at price above the current price may signal a public promotion. Percentage/amount offers are blocked in that case; an explicit fixed loyalty price may compete with the current price. The system does not invent an unknown pre-promotion base price.

The server re-resolves current Shopify variant prices using the existing CRM resolver, then computes eligibility and discounts; browser-provided loyalty fields/costs/prices are not trusted. Normal manual promotion authorization remains intact. Preview is read-only and displays server totals. Saved draft snapshots include original public discounts, selected rules/versions, membership, staff, all order totals and base line identities.

At confirmation, the shared service guard and API verify live product identity/prices, complete lines, frozen quantities/taxes/discounts/totals, current member eligibility and exact configuration/rule state. Changed prices or eligibility require editing and repricing the draft. Confirmation freezes the snapshot and reserves one PENDING redemption per applicable line. Repeated requests cannot reserve another original grant. Existing Shopify draft sync waits for frozen loyalty confirmation; ordinary existing orders retain their old path. Normal existing order synchronization remains responsible for actual orders and payments.

Delivery plus verified payment finalizes savings. Cancellation/test designation releases a pending grant or compensates a finalized grant. Partial refunds add immutable negative events; they never rewrite the grant or financial records. The current canonical refund source is **order-level aggregate**, so reversal allocation is explicitly `ORDER_TOTAL_PRO_RATA`. Reports do not claim SKU-specific returned quantities. Exact line-level refund allocation is a future source-contract enhancement, not silently inferred here.

Physical POS pricing, Shopify storefront discounts and automatic WhatsApp marketing are outside this release. Completed POS history may establish eligibility, but POS carts do not receive loyalty discounts.

## Permissions and screens

Seven sidebar entries: Dashboard, Loyalty Members, Loyalty Products, Redemption History, Historical Customer Migration, Reports and Settings. The existing CRM order editor/WhatsApp order panel shows explicit customer selection, membership summary, history and eligible savings. Existing automatic read receipts, unread counts, manual unread markers and AI paths are unchanged.

Administrators receive existing administrator access. Other staff require explicit grants; legacy unspecified section access grants **no loyalty management permissions**. Staff & Access does not preselect new loyalty grants for legacy users.

- `loyalty.dashboard`: aggregate dashboard.
- `loyalty.members.view`: member list/detail.
- `loyalty.members.manage`: actions, identity resolution, test-history exclusions, local recalculation review; pair with member view for UI access.
- `loyalty.products.manage`: rules/product selection.
- `loyalty.exceptions.approve`: profitability exceptions; requires products management to save rules.
- `loyalty.redemptions.view`: redemption history and ordinary report access.
- `loyalty.reports.profit`: additionally reveals cost/profitability.
- `loyalty.reports.export`: additionally permits CSV exports.
- `loyalty.migration.preview`: previews, manifests and review screen.
- `loyalty.migration.execute`: additionally approves and executes batches.
- `loyalty.settings.manage`: settings screen and policy changes.
- `loyalty.programme.enable`: additionally enables/disables the programme.

CRM quote/customer selectors require existing Sales Orders access and existing create/edit permissions for quoting. Mutations require the existing authenticated session and CSRF token. CSV exports neutralize spreadsheet formula prefixes. Authenticated responses are private/no-store. Rule previews and CRM quotes make no database changes. Members' personal details are restricted to authorized staff; costs are redacted for CRM order users and non-profit-report staff.

## Historical migration and coverage

The implemented historical source is **local CRM history only**, including previously imported Shopify orders and completed POS SalesOrders. A successful migration is not proof that all Shopify history is imported or accessible. No new Shopify permissions or bulk historical import are requested automatically.

An authorized operator can run this aggregate, read-only probe in the deployed service, without importing application startup:

```sh
cd /app
python -m scripts.validate_loyalty_history_readonly
```

It sets the database transaction read-only, prints local aggregate history dates/counts and checks the existing Shopify connection's shop identity, `read_orders` / `read_all_orders` and oldest accessible order date. It never requests customer fields, modifies variables or sends mutations. It reports complete historical coverage unverified even if scopes exist. Review existing import coverage independently; do not interpret the oldest accessible timestamp as completeness. The API version reported is the configured version, not a negotiated-schema compatibility claim.

Management sequence:

1. Keep programme OFF. Review imported history and explicitly resolve identity gaps.
2. Open Historical Customer Migration; set creation-date/source range and unique request key.
3. Preview creates only the review manifest/audit/identity-review records. It does not activate members, enable discounts or send messages. Over 10,000 local orders requires narrower filters; a truncated preview can never execute.
4. Review eligible/existing/new/ineligible/duplicate/missing/ambiguous counts and CSV. Phone matches are suggestions, never auto-associations.
5. Resolve missing links through existing Customer IDs and meaningful management reasons; generate a fresh preview afterwards.
6. Separately approve the exact digest with a reason. Start within 15 minutes; stale unstarted approvals are rejected.
7. Execute batches of at most 100 customers. Repeated or concurrent requests cannot execute a batch twice. Source fingerprints are rechecked; changed histories become REVIEW instead of silently applying the old manifest.
8. Resume RUNNING jobs using the same approved digest. COMPLETE may include reviewed/skipped rows; inspect row statuses, not just job completion.
9. Programme remains OFF throughout migration. Activation is a separate permission and decision.

## Reports

Dashboard defaults to the current **Asia/Colombo business month**, while member/current-rule counts are current state. Transaction date filters apply to finalized redemption timestamps (pending uses reservation time); end bounds are exclusive. Net loyalty sales are discounted revenue of the eligible quantity, not the entire mixed order. Net discounts exclude verified cumulative reversals. Average discount is net discounts per distinct qualifying loyalty order. Repeat rate is lifetime members with at least two qualifying purchases divided by members with at least one. Unknown completion dates/costs/contribution stay unavailable. Growth uses activation time; migration reports use preview creation time. NEEDS_REVIEW redemptions are excluded from verified aggregate sales/profits.

Screenshots (synthetic data):

- [Desktop dashboard](images/loyalty/dashboard-desktop.png)
- [390-pixel mobile dashboard](images/loyalty/dashboard-mobile.png)
- [Mobile member history](images/loyalty/member-mobile.png)

## Deployment and activation (requires separate release approval)

1. Review the PR and limitations above. Back up the database and verify revision `20261123_54` on a staging copy. Confirm existing Shopify imports and local payment histories are reliable.
2. Install existing project dependencies; no new runtime dependency or environment variable is required.
3. Apply `alembic upgrade 20261124_55` using the project's established migration/deployment method. Deploy matching application code only after schema availability. Do not run startup workers against an old schema.
4. Confirm the new singleton is OFF and immutable triggers/indexes exist. Review pages with administrator and restricted staff accounts. No financial data needs changing to validate a disabled programme.
5. The existing Retail background worker (`RETAIL_ENABLED`) must already be enabled before programme activation. Do not enable unrelated production write flags for loyalty. Existing Sales Orders feature authorization continues to govern CRM orders.
6. Configure explicit permissions, cost references and rules; inspect previews. Review historical coverage and execute only separately approved migration manifests.
7. Complete isolated/staging acceptance before authorizing the production programme switch in Settings. Turning the programme on requires both settings and enable permissions plus a meaningful audit reason. Do not manufacture production customer orders for testing.
8. Observe pending/review local recalculations, price drift, discount snapshots and existing CRM synchronization. Existing financial and Shopify safeguards remain the authority.

## Manual acceptance

- Desktop and Android-width pages: navigation, search, filters, pagination, images, bulk offers, readable tables/forms and no horizontal viewport overflow. Test a real Android browser before release.
- First/unpaid/COD-promise/cancelled/test/full-refund history does not qualify. A verified completed purchase enables a later order; the current order cannot qualify itself.
- Partial refund reduces net history and immutable savings correctly; other qualifying purchases retain membership. Missing evidence shows review.
- Shared phones stay separate; explicit identity review, suspension and reinstatement are audited. No silent Customer creation/merge.
- Three discount methods, quantity cap across duplicate lines, expiration, changing Shopify price, missing cost, low margin and approved exception behave correctly.
- Public promotion wins when lower; no stacked discount. Long/multi-line mixed carts use server totals.
- Confirmation blocks changed totals/product identity/prices/rules and freezes the accepted complete snapshot. Network replay produces one order/grant.
- Pending/delivered/verified/reversed ledgers retain immutable events; no loyalty action posts journals, adjusts stock or mutates Shopify products.
- Restricted users cannot manage/enable/export/see profit without the appropriate grants and CSRF; CRM cashiers retain their existing permissions.
- Preview is not activation. Digest mismatch, expired approval and changed source fail safely. Batches resume and never duplicate membership.
- Existing WhatsApp messaging/read/unread/manual-marker behavior, POS receipts, finance, catalogue imports and supplier/inventory functions remain unchanged.

## Rollback and incidents

Preferred rollback is disable programme in Settings with authorized reason, retain all tables/snapshots/audits, stop editing offers and investigate. Frozen commercial prices and immutable redemption history must not be retroactively rewritten. Existing drafts whose quotes have changed require repricing; do not silently remove a historical discount. No Shopify/product/inventory restore is part of loyalty rollback.

For code rollback, first disable the programme, pause only its rollout, preserve/export all loyalty data and verify old application compatibility with additive tables. Rolling code back does not require deleting tables. Existing worker and financial services must not be reset.

Only if explicitly approved after exporting loyalty history: deploy compatible old code before `alembic downgrade 20261123_54`. This drops **all loyalty-only tables**, including their audit history, and is destructive to this module; it is not a normal operational recovery. It does not change original customers/orders/payments or reverse any historical discount. Never downgrade unrelated revisions or reset production finance.

On local recalculation REVIEW, inspect verified source evidence and use the audited management retry after correcting the source through its existing authorized workflow. Never mark unknown payment/refund evidence verified just to clear loyalty review.

## Automated verification

Retrieved results: **532 distinct tests passed**, including **68 loyalty-specific cases**. The SQLite runs skipped two PostgreSQL-only cases; both subsequently passed in dedicated PostgreSQL runs, leaving no unresolved skips or failures. [Per-run counts](loyalty-test-results.json) distinguish overlapping suites. Tests cover eligibility, phone normalization, first/current order exclusion, refunds, promotions, cost exceptions, version/overlap protection, server pricing, immutable snapshots/ledger, access/CSRF/redaction, historical approval/resume/source changes and the read-only probe. Five dedicated PostgreSQL cases exercise additive upgrade/repeat/downgrade, concurrent activation, rule edits, multi-batch migration and confirmation/outbox idempotency. Playwright covers seven pages at desktop/mobile widths and the integrated CRM order path. Existing CRM, WhatsApp, staff-access, payment, fulfillment and finance suites are run separately against local databases.

Physical production activation, complete historical Shopify coverage, real Android acceptance and management cost attestation remain deployment requirements.

Reproducible local test groups (use a disposable database, never production):

```sh
python -m pytest -q tests/test_loyalty.py tests/test_loyalty_postgres.py tests/test_loyalty_browser.py
python -m pytest -q tests/test_staff_access.py tests/test_sales_orders_phase1.py tests/test_sales_orders_phase2.py tests/test_sales_payments_phase4b.py tests/test_sales_fulfillment_phase4d.py tests/test_prompt_xpress_delivery.py tests/test_question_payment_review.py
python -m pytest -q tests/test_crm_whatsapp_inbox.py tests/test_whatsapp_quick_messages.py tests/test_whatsapp_conversation_details.py tests/test_whatsapp_inbox_lead_status.py tests/test_manual_unread.py tests/test_mobile_chat_layout.py tests/test_compact_whatsapp_list.py tests/test_finance_reporting.py tests/test_finance_reporting_pos.py tests/test_whatsapp_phone_country.py tests/test_whatsapp_reply_context.py
python -m pytest -q tests/test_shopify_draft_order_sync.py tests/test_pos_daily.py tests/test_pos_daily_postgres.py tests/test_pos_real_sale_pilot.py tests/test_pos_real_sale_pilot_postgres.py tests/test_pos_receipt_tests.py tests/test_pos_receipt_tests_postgres.py tests/test_purchasing.py tests/test_inventory_phase3.py
```

Set `TEST_POSTGRESQL_URL` to an isolated localhost database named `pixiepinks_test` for the existing PostgreSQL fixtures. Use a separate disposable PostgreSQL `DATABASE_URL` to run the fulfillment and draft-order suites' global-dialect concurrency cases. Set `PGCLIENTENCODING=UTF8` if the disposable cluster uses a non-UTF-8 default. Browser tests use Playwright with `/usr/bin/chromium`. Python compilation and `git diff --check` also passed. Existing framework/deprecation warnings were emitted; they were not counted as test failures.
