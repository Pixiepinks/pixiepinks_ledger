# WhatsApp Inbox customer panel stability

Baseline main: `887af276c290ecee2f553b84788af2d980dbba4a` (merged PR #212). Branch: `feature/inbox-customer-panel-stability`. Migration remains **`20261125_59`**; no new migration, dependencies, worker or environment variable.

**Current owner-authorized loyalty programme is ON. Preserve that exact state and existing product rules. Earlier development-phase OFF instructions do not apply to this release.** No programme, pricing, membership or financial configuration is written by this patch.

## Root cause and measured reproduction

Source inspection and synthetic Chromium reproduction establish the following code path:

1. `whatsapp_inbox.js` refreshes the selected conversation every **4,000 ms** and emits `inbox-conversation-data` on every successful read.
2. The removed listener in `loyalty_order.js` unconditionally cleared `#conversation-loyalty`, started a customer lookup, then reconstructed its heading and rows. It did this even when the selected conversation and data were identical. The empty-to-populated flex item changed the centre column's available message height while the request was pending.
3. That listener expected `data.sales_order.customer_id`, but the existing detail serializer did not return that field. Even a linked commercial order could therefore take the telephone-suggestion lookup path. The correction resolves authoritative order/reviewed Customer IDs on the server instead of treating a telephone match as a link.
4. The profile/order component used a signature of the **entire** conversation object, including unrelated work/unread data. Changing those values reconstructed otherwise unchanged profile/order DOM.
5. Marketing consent independently fetched and replaced its sidebar rows on the same data events. It now coalesces reads and retains unchanged nodes; its permissions, policy and mutation API are unchanged.
6. Existing message reconciliation already retained unchanged message/audio nodes and used scroll anchors. That mechanism is retained rather than replaced or slowed down.

Actual pre-fix release reproduction, at desktop and Android widths: initial load plus **five simulated invocations of the existing message poll** produced **six customer lookups, 15 banner DOM mutations, and replacement of the original heading node**. The after test performs the same six message reads with **one customer projection read, zero unchanged-panel mutations and retained profile/order/customer nodes**. This is an 83.3% reduction in identity requests for that short-window workload, not a production traffic benchmark. Genuine expiry refresh is separately verified after advancing the monotonic clock beyond 12 seconds; a stored suspension then appears.

Exact observed before/after metrics and test results: `inbox-customer-panel-test-results.json`. The source path reproduces the reported symptom; no live production browser/runtime trace was obtained, so an identical live deployment cause is not independently claimed.

## Interface and refresh design

The old pink banner and its container/CSS are removed from the conversation column. There is no hidden duplicate or reserved gap. The existing right panel contains Customer Profile, PixiePinks Loyalty Club, Latest Sales Order and Marketing Consent. Existing Details navigation opens the same panel on mobile. The prior fixed minimum desktop grid could clip the right edge at 1280px even without document-level overflow; responsive three-column tracks and wrapping value spans now keep the complete panel inside the Inbox. Desktop geometry is checked, not just page scroll width.

Profile rows keep their DOM identity and change only when their displayed field changes. Latest order rendering has an order-only signature and does not react to unrelated conversation work state. The order card retains its existing authoritative information, actions and WhatsApp intent fallback. No order state/synchronization behaviour changes.

`GET /crm/whatsapp/api/conversations/{id}/customer-panel` is authenticated, conversation-visibility scoped and private/no-store. It is not a public customer search endpoint. It uses existing normalized-phone and loyalty services, takes an authoritative association only from a conversation-linked order or reviewed attribution, and returns compact fields. A phone-only match remains **Suggested match — confirm when creating an order** and cannot supply pricing/order identity. Multiple candidates are not chosen or merged.

Supported states: Not registered; registered/pending first purchase or activation; active; suspended/ineligible; suggested but unconfirmed; ambiguous; and unavailable with Retry. Network, HTTP, authorization, redirect and invalid-data failures never mean unregistered. An authorization failure clears retained private fields. A transient non-authorization error may retain clearly labelled last-verified fields until a fresh read succeeds. No raw exception appears to staff.

Membership status/count and actual IDs are projected by the canonical service. Lifetime purchases, savings, activation date and View membership require existing `loyalty.members.view`; basic authorized CRM staff receive no history/candidate lists, profitability or unrestricted export. Lifetime totals are not taken from the latest order. New response viewer IDs bind the current page to its authenticated account; account changes cause the core Inbox to clear/reload rather than reuse another account's cache.

The customer controller stores only the **current conversation's** payload in tab memory, keyed by conversation generation and viewer. It coalesces identical in-flight reads, aborts/discards prior-conversation replies, and uses a **12-second monotonic freshness interval** driven by existing data events. No new timer is added. While visible, normal four-second polls pick up expiry, so external purchase/refund/suspension/profile changes normally refresh within roughly 12–16 seconds. Explicit Retry or in-page registration invalidates immediately; a concurrent invalidation schedules at most one follow-up. The same short freshness principle applies to the consent display; successful consent actions force a refresh.

Unchanged data never clears/reloads the panel. On conversation change, prior private information is cleared immediately, before the next response. Cache is not persisted or shared across tabs/users and is cleared/aborted on page exit. Authoritative order pricing/confirmation continues to read and validate fresh server evidence, independently of this informational display cache.

## Playback, drafts, scroll and resource handling

The message reconciliation and near-bottom/anchor policy remain intact. Customer/consent requests do not touch message history or the composer. Tests use a real Chromium audio element playing an in-memory silent WAV at the normal UI-selected speed; unchanged node, playback progression/rate and zero pause events are checked across polling and a new incoming message.

Text drafts and cursor selection survive background refresh. On genuine conversation switches, a bounded 20-conversation tab-memory text cache restores only that conversation's own text; it is cleared on revocation and page exit, with no persistent customer text storage. Quoted-reply context is preserved during polling and cleared on genuine conversation change, as before. Attachments retain their existing safe handling: selecting a pending attachment intentionally hides the text composer, and changing conversation clears the pending file rather than transferring it. Image, video and PDF pending previews/captions are tested.

Active or unsent voice recording cannot be carried to another customer: staff must send or cancel it before switching. Polling still proceeds while recording. Recording tests synthesize an in-browser AudioContext stream, with no physical microphone or outbound send. Normal media upload/status and recorded-video paths remain unchanged.

The original 4-second message poll and 7-second list poll are unchanged. A separate existing 4-second **media delivery-status** timer is legitimate and is not a duplicate message poll. No new worker, socket, notification, automatic send or replay is introduced.

## Test evidence and limitations

Final verification: **538 distinct tests have passing final outcomes**, including 30 new feature cases and 19 PostgreSQL-related cases; zero remaining failures/errors/skips. The final focused reruns resolved the retained original failures. Final distinct outcomes, original failures and affected-suite reruns are recorded in `inbox-customer-panel-test-results.json`. Tests run on disposable SQLite/PostgreSQL and synthetic browser data with mocked external systems. They cover programme ON/OFF, repeated migrations preserving an existing ON setting, read-only PostgreSQL projection, access revocation/other viewer, states, coalescing/expiry/invalidation, message arrival, actual audio, recording, scroll/older-reply anchoring, composer focus/selection, pending media, rapid switching and existing CRM/POS/loyalty/staff/WhatsApp regressions.

Two legacy consent tests delete unlinked synthetic Customers directly. Newer loyalty outbox FKs correctly prevent that unrelated teardown. Those two tests now opt only their disposable Session factory out of outbox creation via the existing processing hook, so the original consent-deletion/invalidation assertions remain meaningful. Production outbox/FKs, customer deletion policy and consent behaviour are not relaxed. An initial local PostgreSQL startup failure was resolved by starting the disposable test server; affected PostgreSQL cases were rerun.

Browser timer tests identify the actual message callback, not every timer with the same interval. A test-only frozen clock was replaced by a real advancing clock plus expiry offset. Media tests respect the existing policy that selected attachments hide the text composer. Physical Android and production traffic/latency remain operator acceptance, not claims from viewport emulation.

## Screenshots (synthetic)

- [Before desktop](images/inbox-customer-panel/before-desktop.png), [before Android width](images/inbox-customer-panel/before-android.png)
- [After desktop, active member](images/inbox-customer-panel/after-desktop-active.png), [after Android width, active member](images/inbox-customer-panel/after-android-active.png)
- [After desktop, unregistered](images/inbox-customer-panel/after-desktop-unregistered.png), [after Android width, unregistered](images/inbox-customer-panel/after-android-unregistered.png)

## Deployment and rollback

1. Review the draft PR, tests and screenshots. Verify actual deployed baseline/migration using existing approved read-only procedures.
2. Record the actual current programme state **ON** and accepted membership/rule baseline. This patch must not change it. Do not use earlier phase runbooks to switch discounts OFF. No new variables, permissions, Meta/Shopify scopes or schema migration are needed.
3. With owner release approval, deploy the reviewed commit through the normal process. Keep the existing migration 59 and all memberships, prices, rules, redemptions, history, stock, POS hardware and consent settings intact.
4. Reload open desktop/Android Inbox tabs. Verify updated Inbox, registration, consent and CSS asset versions (`inbox-stable-1`) plus `inbox_customer_panel.js?v=1`. No automatic customer message or hardware test is required.
5. Run the operator checklist below over repeated real poll cycles. Any financial, registration or outgoing-message action requires its ordinary explicit authorization; use legitimate existing records for passive acceptance. Do not manufacture paid orders, replay events or recover excluded historical orders.
6. Rollback, if separately approved, to the baseline source/image `887af276c290ecee2f553b84788af2d980dbba4a`, which already understands migration 59. Reload browser assets. **Do not downgrade/stamp the database or alter the programme ON state/rules.** No financial reversal, Shopify resend, Customer deletion, outbox reset or printer action is part of rollback.

## Operator acceptance checklist

- Old pink banner and empty spacer are absent; centre message area gains usable height.
- Existing header/actions, AI/Take Over, assignments, lead status, team notes, filters, notifications and manual/red unread controls remain functional.
- Right panel shows profile, numbering country (not physical location), loyalty, latest order/no active order and actual marketing consent controls.
- Unregistered, pending, active, suspended/ineligible, unconfirmed suggestions and ambiguity are accurate. View membership and financial aggregates follow permissions.
- Observe at least several four-second message refreshes and more than one 12-second identity interval. Unchanged text/DOM stays steady; genuine updates appear, and Retry never implies no match.
- Voice playback continues with the same position/rate when messages arrive. Reading old messages keeps its anchor; near-bottom auto-scroll still works.
- Typed text, selection/focus, quoted reply, pending image/video/PDF, recording and upload state remain intact during refresh. Switching customers never transfers private drafts/files/recordings.
- Existing Details button opens the same panel on actual Android/PWA. No horizontal overflow or oversized controls. Desktop keeps three columns.
- Incoming/outgoing text, voice, media, Quick Messages, Saved Voices, products and flows remain normal under their existing restrictions; do not send extra messages merely for this passive UI test.
- Create Order retains explicit confirmation/single suggestion/ambiguous handling, registration idempotency and purchaser/recipient separation.
- Verify existing loyalty discounts, quantities, mixed carts and non-stacking using legitimate authorized workflows. Programme remains ON and accepted membership IDs/prices/redemptions stay unchanged. Leave excluded historical orders untouched.

No live production access, merge, deployment, setting change, Shopify mutation, financial posting, physical hardware command or customer notification was performed for this implementation.
