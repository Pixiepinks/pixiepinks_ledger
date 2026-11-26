# Phase 2: Sales Pipeline daily workspace

Baseline: merged PR #215 (`bad8069`), database revision **20261126_61**.
This refines the existing module; it does not rebuild opportunities or backfill
historical conversations. **No migration, new feature flag or permission change.**

## Implementation

- Desktop is a viewport-height application workspace, scoped to `body.pipeline-page`.
  Header/search/sort/filter, four KPI cards, column headers and the Lead Details
  heading remain visible. Each `.pl-cards` container has its own vertical scroll.
  All five headings fit the tested 1366px laptop layout. Other CRM pages retain
  their original shell/scrolling. Android uses horizontal stages and normal page
  browsing, with a fixed details drawer rather than the desktop height constraint.
- Lead Details separates a fixed selected identity, independently scrolling fields
  and history, and a fixed compact action area. Assignment, copying the number,
  status/dates, chat, customer/order links and the existing Create Order path remain.
  Long names are clamped in the header but shown in full in the scrollable details.
- Readable multilingual typography, existing bundled Sinhala font/fallbacks,
  SVG product/chat/calendar/staff icons, complete priority badges, friendly status
  labels, yellow/purple commitment highlights and dynamic red/orange/amber due
  states. Internal overdue follow-ups are shown on cards and in details. No stage,
  date or message is altered to improve presentation.
- `PAID` without verified local payment evidence is displayed as **Paid —
  Verification Required**. Canonical lead statuses and synchronization are unchanged.
  Unknown amounts display **Amount Not Confirmed**; an all-unknown payment KPI says
  **Amounts Not Recorded**, with its unknown count, rather than implying zero debt.
- Sorting operates over the full authorized filtered projection before slicing
  columns: latest/oldest activity, payment/purchase/follow-up date, overdue, priority,
  highest known value and unassigned. Default payment/purchase ordering keeps
  dated overdue/upcoming commitments first and unconfirmed dates accessible.
  Activity and calendar labels use Asia/Colombo; amounts remain Decimal on the server.
- Each column loads another 50 independently. At 1,000 loaded cards, Next/Previous
  page controls continue through larger filtered sets. KPI totals are never based
  on loaded-card count. Search/filter/sort changes reset page cursors; polling does
  not. Existing global API offset remains compatible.
- Column/card nodes are reconciled by identity; unchanged KPI and report nodes are
  retained. Independent column scroll, details scroll and selected opportunity
  survive ordinary polling. The existing single 15-second pipeline timer remains;
  no extra polling loop was added.
- An edit form binds its original record version. A delayed polling response cannot
  replace that version and cause stale form values to overwrite another staff edit.
  A conflict remains an explicit 409 with the draft input retained.

## Safety and production configuration

Preserve the existing production values:

```
SALES_PIPELINE_ENABLED=true
SALES_PIPELINE_REMINDERS_ENABLED=false
```

The code does not change Railway variables. Do not reset/reapply the completed
backfill. Existing opportunity IDs, commitments, assignments, customers, order links,
audit history, Shopify/payment/inventory/receipt state and concurrency controls are
preserved. No reminder or customer WhatsApp messaging is enabled by this release.
No physical Android or push-delivery claim is made.

## Why Confirmed Orders may be zero

Confirmed-status labels are not sufficient order/payment evidence. The existing
projection requires an explicit same-conversation authoritative local order link
and the existing `convertible()` conditions. An unlinked historical
ORDER_CONFIRMED/DISPATCHED/DELIVERED label is retained and flagged for review, not
used to create an order. `PAID` remains the existing payment-stage mapping unless
staff explicitly update it through the approved workflow.

**Actual production reconciliation has not been executed here:** no authorized
Railway runtime is connected. Counts in the owner's acceptance evidence are not
hardcoded or presented as fresh diagnostic findings.

### Immediate read-only check on deployed revision 61

[Copy the complete Console command](../scripts/sales_pipeline_orders_console.txt)
into the authorized Railway CRM browser Console. It is self-contained and imports
only modules already present in PR #215; it does not require deploying Phase 2.
It imports no application startup, sets PostgreSQL **TRANSACTION READ ONLY** and a
30-second statement timeout, prints aggregate sanitized JSON and rolls back.
It does not print names, numbers, messages, tokens or payment details.

After an approved Phase 2 deployment the shorter equivalent is:

```bash
cd /app
python -m scripts.validate_sales_pipeline_orders_readonly
```

An authorized administrator may request local record IDs/statuses for review:

```bash
python -m scripts.validate_sales_pipeline_orders_readonly --details
```

Output distinguishes local association verification, missing links, missing or
ineligible recorded orders, ambiguous exact-conversation candidates, single
unlinked candidates requiring review, and `PAID` labels without verified local
receipts. Details explain source-stage exclusions separately from link validity.
The diagnostic examines current active opportunities with historical confirmed,
dispatched, delivered or paid lead statuses; it is not a live Shopify read-back or
proof that an order is paid. No association, payment, order or stock is mutated.

### Separate administrator review

1. Review the sanitized summary first. Use `--details` only in the authorized runtime.
2. Open the referenced existing conversation and Sales Order. Verify purchaser,
   conversation identity, actual status and existing Shopify/reconciliation evidence.
3. For ambiguous candidates, stop for an explicit identity decision. Never create an
   order merely to fill the Confirmed column or infer payment from a status label.
4. Only after separate owner approval, use the existing permission-checked order
   association/status workflow. Its identity/version/reconciliation checks remain.
   No production reconciliation write is part of this implementation or diagnostic.

## Tailwind production warning

The base template loads the Tailwind CDN for legacy pages. The pipeline already
uses explicit production `crm.css` plus its own stylesheet and needs no Tailwind
utility compilation. The CDN is omitted **only on Sales Pipeline routes**, verified
in Chromium, so this module avoids the production-CDN warning without migrating
unrelated pages. Other pages still need a separately approved compiled/static CSS
migration if the application-wide warning is to be removed. Phase 2 assets have a
cache-busting version suffix.

## Visual evidence

`docs/images/sales-pipeline-refinement/before/` contains the original PR #215
**synthetic** rendered baseline at desktop/laptop/mobile sizes; these are not
production customer screenshots. No additional live-production screenshot files
were supplied with this request.

`after/` contains actual rendered captures using 1,015 synthetic opportunities,
including 803 Interested / Follow-up records. Captures include all requested
viewport sizes, selected/unselected states, independently scrolled columns,
multilingual/long-name and overdue content, payment/purchase editing, unknown
amounts and Android details. Fixtures deliberately use different counts from live
acceptance evidence. The original reference guides the four KPI/five-column/detail
composition, colors, borders and hierarchy. Existing PixiePinks navigation is kept.

Remaining visual differences: existing CRM sidebar width/branding, data-dependent
text and a compact two-column action footer. Laptop name/priority layout adapts to
keep all five headings readable. No browser zoom/transform fitting, 100% pixel-match
claim or physical Android-device validation is used.

Reproduction (disposable local data only):

```bash
DATABASE_URL=sqlite:////tmp/pipeline-visual-phase2.db python scripts/sales_pipeline_visual_fixture.py
# While that isolated local fixture is running, in another terminal:
DATABASE_URL=sqlite:////tmp/pipeline-visual-phase2.db python -m scripts.seed_sales_pipeline_scale --count 1000
python scripts/capture_sales_pipeline_refinement.py
```

Fixture scripts refuse production/non-/tmp/non-SQLite targets, suppress application
startup workers, and use a documented synthetic login. They never run as part of
normal application startup. Stop the local fixture after capture.

## Performance and tests

**224 distinct tests passed; zero failures or skips in the final retrieved reports.**
See `sales-pipeline-refinement-results.json` for suite counts and retrieved results.
Measured projections: 1,001 records in 0.2490 seconds and 5,001 records in
0.5787 seconds, each using seven SQL statements. Synthetic tests
cover 1,001/801 interested and 5,001/4,001 interested records. Projection query count,
elapsed time, response bytes, complete KPI counts and pagination disjointness are
measured. Only paginated cards reach the browser; full historical messages are not
downloaded. The existing explicit 10,000-opportunity projection budget remains.
Measurements are disposable SQLite development results, **not a production SLA**.

Chromium tests verify at 1648×928, 1920×1080, 1366×768 and 412×915:
independent scroll positions, no desktop page overflow, all five desktop headings,
fixed details actions, stable DOM/polling, repeated per-column loading and no mobile
page-wide horizontal overflow. Another test deliberately delays a detail response
while a different staff edit commits and verifies the stale form is rejected.
PostgreSQL tests prove the exact Console command runs READ ONLY against revision
61 and leaves records unchanged. Existing Inbox/unread/notification, customer,
Sales Order/payment/excess and pipeline safeguards are regressed separately.

## Deployment and rollback

1. Review the draft PR, screenshots and results; back up through the normal release
   procedure. No new migration is required: retain **20261126_61**.
2. Deploy only after owner approval. Keep the enabled pipeline and disabled reminders
   exactly as configured. Do not run any backfill, reconciliation writer or seed script.
3. Reload the pipeline assets. Check all three desktop sizes and an authorized Android
   browser, using existing authorized records without changing them for a smoke test.
4. Run the read-only order summary if approved, review candidate reasons, and keep any
   subsequent association approval separate. Do not enable reminders during UI release.
5. Acceptance: column scrolling leaves other columns/details fixed; actions remain
   visible; long names/notes, overdue dates, unknown totals and sorting are clear;
   existing edits, conflicts and original financial/order controls still work.

Rollback: redeploy the previous accepted PR #215 code/commit through normal approved
release controls, retaining migration 61 and all production variables/data. No schema
or record rollback, deletion, re-backfill or commitment reset is needed. Previous UI
scrolling returns, but current opportunity history and financial state remain intact.

Remaining operator checks: live read-only reconciliation findings, production release
approval and physical Android visual/touch acceptance. Staff reminder delivery remains
OFF and outside this release.
