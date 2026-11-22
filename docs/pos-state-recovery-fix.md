# POS browser state and register-session recovery

## Findings

Reviewed main `96d3b44eec789e72dcbe932140c1ca4b09168a8f` (Phase 23).

1. `static/pos.js` startup unconditionally called `showSale` for `sessionStorage['pixiepinks-pos-sale']`. The key was shared across staff logins within a browser tab. Closing the modal did not clear it; a legacy cart could also restore the same checkout. This explains repeated completed-sale popups and cross-account restoration.
2. `/crm/pos/api/context` already queries OPEN sessions by current shop **and authenticated cashier ID**. `open_register` already serializes opening, enforces unique active register/cashier constraints, returns an existing same-owner session and rejects another owner. No ownership or financial bypass is needed.
3. The dashboard's initial `Open a register to start a sale` message was never updated by successful context restoration or register opening. The header could correctly show an OPEN session while the main instruction still said to open one. Opening also created a cart before showing success; a later cart failure could mask a successfully persisted opening. These are reproduced UI defects. Amanda's actual production context response has not been accessed here; do not infer a different shop/identity problem or modify her existing session as a workaround.

## Changes

- Use shop/cashier-scoped cart and sale references. Only same-session legacy carts and server-confirmed **owned unresolved** legacy sale IDs are adopted. Ignore legacy completed-sale auto-restoration without deleting server records.
- After a new checkout, retain existing live status/completion modal. Closing COMPLETE clears only that sale's browser recovery pointer. Refresh never automatically opens COMPLETE/CANCELLED; completed transactions remain accessible manually through Sales & returns with existing report permissions.
- Incomplete/QUEUED/REVIEW/UNCERTAIN/pending-payment references survive dismissal. Context additionally returns owned unresolved references, displayed as explicit recovery buttons even if browser storage was lost or another cart is active. No status, financial row or print job is changed to do this.
- Restore only authenticated owner's OPEN session. Display loading first, then clear register-open readiness/float; explicitly confirm opening. Create a cart on normal product/cart interaction rather than using cart creation as the register-opening success criterion. Discard only in-memory carts if the session changes; held/server records remain intact.
- Existing duplicate-session, float, handover, financial REVIEW and printer safeguards are unchanged.

## Deployment and read-only verification

No migration, environment-variable, Shopify, Windows connector, printer or financial configuration change is needed. Merge/deployment requires owner approval. POS JavaScript cache version is updated; refresh the POS page after the approved deployment. Do not reopen, close, reassign or recreate Amanda's production session to apply this fix.

For an authorized read-only check, sign in as Amanda in the browser, open developer tools Console on the CRM origin, and run:

```javascript
fetch('/crm/pos/api/context', {credentials:'same-origin', cache:'no-store'})
  .then(async r => {
    if (!r.ok) throw new Error('Context HTTP ' + r.status);
    const c = await r.json();
    console.log(JSON.stringify({shop:c.shop, cashier_id:c.cashier_id,
      sessions:c.sessions.map(s => ({id:s.id, register_id:s.register_id,
        cashier_id:s.cashier_id, status:s.status, opening_float:s.opening_float,
        expected_cash:s.expected_cash}))}, null, 2));
  });
```

This calls the ordinary authenticated read-only context endpoint and selects only identity/session fields; it does not print tokens or customer/payment data. Expect cashier 3's existing session `55871573-8e78-488d-b57d-e51b590f37ed`, register 1, OPEN, with unchanged zero float/cash, if that remains the current live state. If absent, stop: capture only shop/cashier/session identifiers and HTTP status for investigation; do not alter ownership or create another session. An administrator's context must not adopt Amanda's OPEN session.

Check completed order #1810 does not automatically open on refresh or staff account change. Manually inspect it through Sales & returns without printing, reconciling or retrying checkout. Unresolved references must remain visible. Future checkout completion and opening/handover acceptance can use disposable fixtures; production financial/hardware actions require separate approval.

## Rollback

Revert this PR through an approved code release and refresh the browser. No database rollback is needed. Do not clear the database, jobs, journal, sessions or financial history. New browser keys contain only IDs, not credentials; reverting may restore the prior popup/message defect. Existing server-held and unresolved records remain durable regardless of browser storage.

## Verification

- `tests/test_pos_state_recovery.py`: **11 passed** (Admin/cashier completion, dismiss/refresh/login, cross-account isolation, QUEUED/REVIEW/UNCERTAIN/payment recovery, zero-float opening, double-click, conflict, handover, post-commit context failure and shop-scoped SELECT-only lookup).
- Existing daily POS, basic POS, pilot, synthetic receipts, reconciliation UI, provisional/browser receipts, protected customer readback, finance reporting and WhatsApp selection: **141 passed**.
- Disposable PostgreSQL `tests/test_pos_postgres.py tests/test_pos_daily_postgres.py`: **6 passed**, including active-register uniqueness and competing checkout/worker safeguards.
- **158 passed; zero remaining failures or skips.** Existing deprecation warnings remain. JavaScript syntax, Python compilation and diff whitespace checks passed. No production database, Shopify or printer was used.
- Older browser assertions that demanded completed-popup restoration were updated to require the dashboard and explicit history navigation. The provisional test now distinguishes the existing read-only hardware status poll from prohibited hardware writes; its financial/print-job snapshot checks remain intact.
