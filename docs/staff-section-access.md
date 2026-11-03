# Administrator-created staff accounts and section access

Open **Staff & Access** in the administrator sidebar (`/admin/staff`). Create a
username and initial password, tick permitted sections, then create the account.
New accounts are STAFF, never ADMIN, and start with no sections selected. Expand
an account to edit its sections, reset its password or untick Account active.
Staff with no sections land on a page explaining that access must be assigned.

All 18 requested sections are supported. The sidebar hides inaccessible links,
but the security boundary is the shared server authentication dependency. It
checks the database for every protected request and denies ungranted sections,
including mutations and APIs such as /api/sales-orders and /api/fulfillments.
Unknown future sections fail closed for managed accounts. Grants are not trusted
from session cookies, query parameters, role form fields or the browser.

Inbox Quick Messages requires both WhatsApp and Quick Messages grants. Sending a
Saved Voice requires both WhatsApp and Saved Voices. The standalone Quick
Messages and Saved Voices sections allow their normal management operations;
select these grants for staff who need the corresponding Inbox features. CRM
lead editing requires the Leads grant. Product selection and ordinary manual text,
audio and attachment messaging are included in the WhatsApp grant. Some shared
screens show contextual customer/order summaries; these are section permissions,
not field-level redaction or per-customer assignment.

Sales Orders grants normal create/edit/transition/payment-claim/dispatch actions;
Fulfillments grants ordinary fulfillment operations. Existing role-specific
privileges are preserved for legacy managers when those sections are assigned.
Administrative payment verification, restricted Shopify/accounting operations,
Global AI control, owner-only voice diagnostics and learning administrator actions
retain their existing administrator checks. Section access does not turn a STAFF
account into ADMIN. Existing feature flags still determine feature availability.

Administrators retain full access and cannot be edited/deactivated through this
staff page, preventing owner lockout or accidental administrator demotion. There
is no delete-account action: historical messages and audit references stay intact.
Passwords are hashed using existing bcrypt, never displayed in account lists or
recorded in access audits. Passwords need 12 characters, at most 72 UTF-8 bytes.
The page supports duplicate-name checks, CSRF validation and inline create errors.

Every successful create/update is audited with administrator and target IDs,
timestamp, previous/new grants, active state and whether a password reset happened
(never the password/hash). Display times use Asia/Colombo. Audit is admin-only.
Updates lock and compare an expected account version, rejecting stale concurrent
forms. Database-backed session versions invalidate all old staff sessions on any
save; deactivation also prevents login. Staff sign in again to use their new
access. Checks apply to new requests; an already-running operation may finish.

## Migration and compatibility

Alembic revision **20261103_22**, following 20261030_21, adds users.section_access,
users.is_active, users.session_version and staff_access_audits. Existing accounts
keep NULL grants (legacy role behavior), remain active, and retain their passwords.
New frontend-created accounts always have an explicit allow-list, even when empty.
Editing a legacy account opts it into the checked-section policy. Existing admin
cookies with no version continue to work at version zero. No AI, voice, order or
accounting records/settings are migrated or modified.

## Railway rollout and review

1. Back up PostgreSQL, review/merge the PR and deploy the reviewed commit. The
   existing application startup runs Alembic; an operator can verify with
   `python -m alembic current` (expect 20261103_22). No new dependency or env flag.
2. Confirm every web instance/worker is running the new commit and drain old web
   processes **before creating staff accounts**. Old application versions lack
   this section enforcement. A successful push alone is not deployment evidence.
3. Log in with the existing administrator account, open Staff & Access and create
   a staff account with WhatsApp, Quick Messages, Saved Voices and Leads selected
   as appropriate. Do not use the live account to send customer test messages.
4. In a separate private browser session, log in as staff. Confirm only granted
   navigation links appear and an ungranted finance URL/API returns 403. Verify
   allowed normal workflows with owner-controlled test data.
5. Change grants, reset the password and deactivate the test account; verify old
   sessions cannot continue and inactive/password-reset login behavior is correct.
   Review the audit and re-enable/regrant only intended sections.
6. Keep the existing Global AI state and voice encoder settings unchanged. This
   release does not enable AI, send messages, or change learning consent.

The annotation about conversation ownership motivated staff accounts; this change
does not assign conversations, add Mine/Unassigned queues or restrict staff to
assigned customers. Those are a separate Inbox workflow after account setup.

## Rollback

Prefer keeping section enforcement deployed and fixing forward. **Do not simply
revert to a version without access checks while staff accounts can authenticate.**
That older version does not enforce is_active, grants or session_version. If a
pre-permission rollback is unavoidable, use maintenance access controls, revoke
staff credentials and invalidate existing sessions with an approved session-secret
rotation before exposing the old version. Revoking only the new session version
is insufficient for old code. Preserve/export the audit before considering a
schema downgrade; removing the new columns/table discards permission/audit state.
No rollback or secret/password change is performed by this implementation.

## Verification

The full relevant regression suite passed **150 tests** with task-owned SQLite
and PostgreSQL. It includes all 18 section grants, denied direct links/APIs,
new-user login/landing, empty grants, protected administrator accounts, CSRF,
password resets, deactivation, cross-session revocation, audit privacy and stale
updates. PostgreSQL verifies the additive migration/repeated upgrade preserves
legacy accounts and simultaneous administrator saves produce one committed update
and one conflict. Chromium creates an actual staff account through the checkbox UI.

Follow-up staff/Inbox/AI tests after final session handling passed **68 tests,
2 skipped** (PostgreSQL-specific checks were not enabled in that follow-up; they
were enabled in the 150-test run). Existing Inbox, Quick Messages, Saved Voices,
learning migrations, sales-order permissions, payment and fulfillment guards are
included. All provider calls were mocked. No production deployment, account
creation, messages, accounting postings or AI setting changes were performed.

An additional focused account/audit failure run passed 4 tests, including a
commit failure that rolls back grants, password and session version together and
does not create a false audit event. Route dependency inspection confirms every
mapped section route includes the shared server authentication/access dependency.
