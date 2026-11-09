# Ad → Staff assignment rules

Administrators can configure **Marketing → Ad Assignments** (`/crm/ad-assignments`). This feature uses stored WhatsApp advertisement referrals. It does not need a Meta Ads token, Ads Insights permissions or successful Ads Analytics synchronization. It adds no network integration or paid service.

## Frontend workflow

1. Choose a recorded advertisement (up to the most recent 500 Ad IDs with referrals), or enter its numeric Meta Ad ID for an advertisement that has not received a message yet.
2. Enter an optional readable rule name, choose an active staff account with WhatsApp Inbox access, and leave **Enable automatic assignment** checked.
3. Save the rule. Future matching ad referrals assign unassigned conversations to that staff member. Existing conversations are not changed merely by saving a rule.
4. Use Edit to change the target or disable a rule. Only one rule exists for each Ad ID. Disabled rules and all history are retained.
5. Use History to inspect rule changes and recent automatic assignments. Assigned staff see the conversation through the existing Mine inbox and existing unread/access rules.

Administrators also get an **Assign staff** shortcut on individual advertisement rows in Ad Analytics. The shortcut opens the matching rule or prefills a new rule with that Ad ID. The routing page itself remains available even when Ads synchronization is disabled.

## Routing behavior

- Requires an actual, signed inbound WhatsApp delivery with referral `source_type=ad` and an exact numeric source ID matching an enabled rule. It does not guess from campaign names, product interests, creative images, links or ordinary message text.
- Signature verification uses the existing `META_APP_SECRET` webhook path. If signature verification is not configured, live automatic routing is paused and the frontend explains this. No secret is displayed. This change does not configure or overwrite any credential.
- Applies to newly created conversations and existing unassigned conversations when they receive a new matching ad referral.
- An already assigned conversation keeps its owner, even if the customer later clicks another advertisement. A rule change does not migrate existing owners.
- An unmatched ad, non-ad referral, disabled rule, invalid ID, or staff member who is inactive/no longer allowed Inbox access leaves the conversation unassigned. Eligibility is rechecked at execution, not just at rule creation. There is no fallback to a different staff member.
- Out-of-order older ad referrals do not override newer recorded ad evidence for an unassigned conversation. Exact message/referral association is checked.
- Rule matching uses the inbox's existing normalized conversation number and saved message/referral association. No customer, lead or referral copies are created.
- The assignment, its history, the inbound message/referral and processed delivery receipt share one database transaction. The existing receipt uniqueness handles Meta retries; a duplicate delivery creates no second assignment/history event. Audit/storage failure rolls back registration and returns the existing retryable webhook error.
- Automatic routing uses the existing assignment version and an atomic “still unassigned” update. PostgreSQL row locks follow conversation → rule → target-user ordering. Existing manual assignment version checks remain in force. Concurrent ownership changes cannot be silently overwritten.
- Assignment does not change conversation AI/HUMAN mode, Global AI configuration, resolution state, lead status, follow-up scheduling, consent, Saved Voices, orders, payment or question automation. Normal inbound handling still runs as before. Assignment itself sends no WhatsApp message.

## Existing unassigned conversations — explicit optional action

Each enabled, eligible rule has **Apply to existing unassigned chats**. Nothing happens until an administrator previews a batch, checks the confirmation box and submits it.

Candidates must currently be unassigned and have that exact Ad ID as their **latest recorded ad referral**. A newer referral to another ad excludes the chat. Non-ad messages do not change which ad referral is latest. The action uses existing historical metadata under administrator review; it cannot retrospectively prove whether legacy records were signature-verified.

Each preview shows the first **200** matching conversations and the total remaining count. The confirmation includes the rule version and a fingerprint of the previewed conversation IDs, assignment versions and latest referral message IDs. If the rule, ownership or relevant latest referral changes, the server returns 409 and requests a fresh preview. Staff eligibility and enabled state are checked again. Already assigned chats are never overwritten.

A batch is atomic and audited; repeat preview/apply for remaining chats. The server does not expose an unbounded “assign everything” operation. This historical action changes ownership only and sends no customer messages.

## Audit and access

Only authenticated administrators can view, create, edit or apply these rules. The server enforces this on every route; CSRF protection covers writes. Even staff with Ad Analytics viewing permission cannot administer routing rules. Reports/responses are private/no-store. Staff retain their existing conversation ownership/sharing permissions.

`ad_assignment_rule_audits` records each configuration version and administrator, with before/after staff, enabled state and label. Automatic ownership events use the existing `conversation_assignment_audits` table:

- `source=AD_RULE`: live automatic referral routing. The actor references the administrator who last configured the rule; it does not imply they manually assigned that individual chat.
- `source=AD_RULE_EXISTING`: explicit administrator-reviewed historical batch.
- `source=MANUAL`: existing manual assignment actions/history.

Ad ID, rule ID and rule version are retained with the ownership event. The existing individual assignment-history API includes these additive fields. The frontend rule history shows configuration changes and the latest 50 rule-attributed ownership events. Rules are disabled rather than deleted so historical evidence remains meaningful.

## Deployment and migration

Revision **20261109_37**, following **20261109_36**, creates:

- `ad_assignment_rules`: unique Ad ID, staff target, enabled state, optimistic version and administrator/timestamp fields.
- `ad_assignment_rule_audits`: immutable configuration version events.
- Additive `source`, `ad_id`, `rule_id`, `rule_version` fields on existing conversation assignment history, plus a rule-history index. Legacy events receive source MANUAL.

Apply `alembic upgrade head` before deploying the updated application through the normal reviewed process. The migration is idempotent and does not create rules, assign old conversations, change permissions or update AI/consent settings. Existing owners and history are preserved. No new environment variables are required; the existing webhook signing configuration is used.

Rollback: disable rules in the frontend if keeping the feature deployed, or redeploy the previous application revision. Retain the additive tables/columns; downgrade intentionally preserves audit/rule history. Assignments already completed remain normal conversation ownership and can be changed through the existing manual assignment workflow. No automatic merge, deployment or production reassignment is part of development delivery.

## Changed files

`models.py`, `alembic/versions/20261109_37_ad_assignment.py`, `ad_assignment_service.py`, `ad_assignment_routes.py`, `main.py`, `conversation_assignment_routes.py`, `staff_access.py`, `templates/base.html`, `templates/ad_assignments.html`, `static/ad_assignments.js`, `static/ad_assignments.css`, `templates/meta_ads.html`, `static/meta_ads.js`, and the new assignment tests/documentation.

## Validation scope

Tests use isolated SQLite databases, signed synthetic webhooks, disabled Global AI, mocked sends and local Chromium. No live Meta calls or production changes are made. They cover duplicate deliveries, pre-existing owners, rule versions, signatures, eligibility changes, out-of-order referrals, media, bounded/stale historical previews, concurrency, transactional rollback, administrator authorization/CSRF, migration and mobile/desktop UI. PostgreSQL lock semantics require the existing PostgreSQL regression environment for a real-server concurrency check; the local concurrency tests use SQLite atomic version updates.

### Recorded test results

- **29 new feature tests passed**, including signed text/media deliveries, duplicate receipts, staff visibility, manual-owner preservation, eligibility changes, configuration version conflicts, stale/bounded batch application, concurrent assignment, audit rollback, additive migration and Chromium desktop/390px/320px layouts.
- **135 existing regressions passed** across conversation ownership, referral capture, Ads Analytics UI and shared route authentication.
- One pre-existing test failure was reproduced independently on unchanged main `3f5b2ca`: `test_private_referral_fields_are_not_exposed_in_inbox_api_and_requires_signed_webhook` expects a staff member to read an unassigned conversation (200), but current ownership permissions correctly return 404. This branch leaves that permission behavior intact.
- Python/JavaScript syntax checks and `git diff --check` passed. No live services or production records were used.
