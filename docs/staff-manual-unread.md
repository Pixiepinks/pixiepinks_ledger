# Staff-coloured manual unread reminders

## Behaviour and permissions

The existing red numbered unread badge remains actual incoming-message state. Opening a conversation still invokes the existing read endpoint exactly as before. Manual markers are independent internal reminders: opening, new messages, login/logout and restarts do not clear them. They do not send Meta requests, change assignment, workflow or notification state, or increase unread totals.

Each authorised Inbox viewer sees all staff markers on conversations they can already access. Markers never expose otherwise-hidden conversations. Existing All/Mine/Shared with me/Unassigned permission rules remain unchanged. Shared read-only viewers may set/clear their own reminders without receiving reply, assignment or AI privileges. Staff can clear only their own marker; administrators can clear any visible marker through the named menu entry.

The `Marked Unread` filter combines with existing mailbox/search/workload/lead filters. It uses an EXISTS query before limit/offset, so multiple markers do not duplicate a conversation or alter pagination. Existing automatic Unread filtering is unchanged. Both filters may be selected together.

Use the row's three-dot menu or the conversation header menu. Names are available in the menu, through hover tooltips, accessible dot labels, and dot taps. Markers use blue/purple/orange/green/pink dark colours; names distinguish people even when palette colours repeat. Header dots scroll horizontally if many staff mark a chat; the menu lists every staff name. The list updates through existing seven-second polling; open list menus defer background list replacement. The selected header uses existing four-second polling. Updates from other users therefore are not instantaneous.

## Storage and concurrency

Migration **20261123_54**, following `20261122_53`, adds:

- `users.unread_indicator_colour`: stored palette name. Existing accounts get a deterministic `(id-1) mod 5` palette assignment. New accounts created in Staff & Access receive the same rule. No passwords, access grants or session versions change.
- `conversation_manual_unread`: composite primary key `(conversation_id, staff_id)` provides the unique constraint; staff index supports lookups. Active markers are separate from messages/conversation counters.
- `conversation_manual_unread_audits`: append-only application audit for actual MARKED/CLEARED transitions, actor and marker owner. Clearing removes only the active reminder; its audit remains. Repeated identical requests do not duplicate transitions.

The server checks section access and current conversation visibility, locks the conversation to serialize reminder changes on PostgreSQL, and uses conflict-safe insertion/deletion. All actions require session authentication and CSRF. There is no API to forge another person's marker. Administrator colour updates validate the fixed palette, compare the previously observed colour, and audit changes in the existing Staff Access audit. Colour-only changes also work for administrator accounts without granting access to protected account settings or resetting their sessions.

Conversation list markers are fetched with one batched joined query for the returned page, not a query per marker or per conversation. Detail responses include the same `manual_unread` array. Marker responses contain staff IDs/usernames and palette names, never customer message contents or tokens. No new runtime dependencies or variables are required.

## Administrator colour configuration

Staff & Access → expand the staff account → Unread Indicator Colour → select palette colour → Save unread colour. Administrator login is required. Other account access/password controls remain separate and retain their existing protections. Existing markers use the current stored colour on the next poll.

## Review and deployment

No production deployment or migration was executed during development.

1. Review/approve the PR and take the standard database backup. Confirm current migration is `20261122_53` and no conflicting migration has been released. Rebase/review the revision link if main has advanced.
2. Apply the additive migration using the normal approved release procedure: `python -m alembic upgrade head`. Coordinate application deployment so code requiring the new column/tables does not serve requests before migration completion. Existing old application code can run against the additive schema.
3. Deploy the reviewed release and reload browser assets. No Railway/Meta/POS/printing variables need changing.
4. Verify `python -m alembic current` reports `20261123_54`. Use the acceptance checklist below with authorised internal accounts.

## Rollback

Prefer redeploying the prior application while retaining the additive schema and all marker/audit data. This restores the old UI without altering actual unread counts or messages.

A schema downgrade is **optional and destructive to this feature's data**. Only after separately approving/exporting marker, audit and colour data, stopping the new application and deploying compatible old code, run `python -m alembic downgrade 20261122_53`. It drops only the two new tables and colour column. It does not change conversation/message state or access grants. Do not downgrade while new code is serving requests. PostgreSQL upgrade, repeated upgrade, concurrent inserts and downgrade preservation are tested on a disposable schema. SQLite rollback uses native DROP COLUMN (SQLite 3.35+), retaining referenced user rows without disabling foreign keys or rebuilding users. Older SQLite must retain the additive schema or be upgraded before a schema downgrade. A foreign-key-enabled rollback regression covers retained users, conversations and existing access audits.

## Desktop and Android acceptance

Use two staff accounts with legitimately assigned/shared access plus an administrator. Do not broaden conversation access merely to run this checklist.

- Record a conversation's real red count. Staff A marks it through the list menu: one named coloured dot appears without increasing that count or notifications.
- Open the conversation: actual unread messages clear as before; the manual dot remains. Reload, log out/in and reopen: the marker persists.
- Staff B marks the same accessible conversation: two named dots appear. A clears their own mark: B's remains. A cannot clear B's mark by API. Administrator can clear B's named mark.
- Check the header menu, Android tap names, mouse tooltips, keyboard focus and screen-reader labels. Check scrolling and narrow-screen menus, including Team Notes and existing mobile chat controls.
- Change staff colour as administrator and wait for polling. Non-admin and invalid-CSRF attempts must be rejected. Password/access/session settings remain unchanged.
- Apply Marked Unread with All/Mine/Shared/Unassigned as permitted, search and pagination; multiple staff markers must not duplicate rows. Compare the normal Unread filter and red/notification totals.
- Receive a genuine new message through normal operations: the automatic unread count increases normally while reminders persist. Do not replay prior webhook payloads.
- Verify inaccessible conversations remain inaccessible. Shared read-only access must not gain sending/AI/assignment rights.

Automated Chromium desktop/mobile viewport tests use synthetic data. Real Android acceptance is still an operator step; no physical Android claim is made.

## Reproduce automated verification

Use disposable local databases only. The migration tests create and remove unique schemas; never supply a production URL. The primary local test database must be named `pixiepinks_test`; the separate staff-access migration test requires `staff_access_test`.

```sh
DATABASE_URL=sqlite:////tmp/pixiepinks-unread-tests.db \
AD_FLOW_TEST_DATABASE_URL=postgresql://agent@127.0.0.1:55429/pixiepinks_test \
TEST_POSTGRESQL_URL=postgresql://agent@127.0.0.1:55429/pixiepinks_test \
python -m pytest -q tests/test_manual_unread.py tests/test_whatsapp*.py \
 tests/test_inbox*.py tests/test_compact_whatsapp_list.py tests/test_conversation*.py \
 tests/test_crm_whatsapp*.py tests/test_staff_notifications*.py tests/test_staff_access.py \
 tests/test_staff_quoted_replies.py tests/test_single_row_filters.py \
 tests/test_mobile_back_navigation.py tests/test_mobile_chat_layout.py

DATABASE_URL=sqlite:////tmp/pixiepinks-staff-access-tests.db \
STAFF_ACCESS_TEST_DATABASE_URL=postgresql://agent@127.0.0.1:55429/staff_access_test \
python -m pytest -q tests/test_staff_access_postgres.py
```

Browser cases require the existing Playwright/Chromium test dependencies. No new production dependency is introduced. Migration coverage includes repeatable upgrade and downgrade, deterministic defaults, simultaneous duplicate marker requests, preserved automatic unread counts, legacy user/access data and existing foreign-key references. Other tests cover independent staff markers, authorised shared viewers, owner/admin-only clearing, CSRF and revoked section access, notification-count integrity, genuine incoming messages, logout/login, refresh, filtering/pagination and desktop/mobile controls.

Some older fixture accounts were unassigned despite the current Inbox visibility rules; their synthetic conversations now have explicit owners. The historical lead-link migration's deliberate SQLite rollback refusal is tested directly, avoiding unrelated later batch downgrades. The new unread migration's own SQLite and PostgreSQL rollback paths are tested separately. Production authorization and historical migration implementations are unchanged.

## Retrieved verification results — 23 November 2026

- Full relevant WhatsApp/Inbox suite: **680 executed; 679 passed and one mobile-resize timing assertion failed initially**.
- After awaiting the existing asynchronous viewport update, affected manual-marker/pagination/mobile suite: **13 passed** (overlaps the full run and includes the previously failing case).
- Separate staff-account PostgreSQL migration/concurrent-admin test: **1 passed**.
- **681 distinct test cases have retrieved passing results across these runs; no unresolved failures or skips.** This is not a claim that the initial 680-case invocation was entirely green.
- Includes PostgreSQL concurrency, repeatable migration/downgrade, FK-enabled SQLite rollback, automatic read/unread integrity, notifications, permissions, incoming messages, refresh/logout and desktop/mobile Chromium. Existing deprecation warnings remain. Syntax and whitespace checks pass.
