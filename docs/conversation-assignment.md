# Conversation owners and private staff inboxes

Administrators have **Mine**, **Shared with me**, **Unassigned** and **All** ownership views, separate
from the existing unread/AI/human/payment status filters. Open a chat, choose an
active account in Owner, and click Save owner. Choose Unassigned to clear it.
Admins can assign chats to themselves. Eligible owners need WhatsApp Inbox access.
Deactivated accounts or accounts without Inbox access cannot receive new chats;
their existing ownership is retained until an admin deliberately reassigns it.

Every non-ADMIN account (including existing managers/agents) sees only assigned
conversations or chats explicitly shared by an admin. Staff have Mine and Shared
with me, cannot fetch
All/Unassigned, cannot enumerate assignees, and cannot claim/reassign conversations.
The database scopes list/search/results/unread counts and the WhatsApp lead list,
and the central authenticated dependency checks conversation-specific paths before
reading or mutating anything. Direct details, reply-jump context, referrals,
attachments, voice playback, sends, status receipts, read/mode changes and linked
WhatsApp lead endpoints reject chats with neither ownership nor an explicit share with a non-disclosing
404. These checks also cover legacy accounts with NULL section grants.

See [private notes and explicit sharing](inbox-collaboration.md) for read-only
versus reply grants, revocation and the latest deployment migration.

The existing section permission is still required: ownership does not grant Inbox,
Leads, Saved Voices or other module access. CRM Dashboard WhatsApp summaries are
scoped too. Existing business/customer/order/learning module permissions remain
their separate section policies; this is conversation privacy, not a complete
per-customer sales/accounting permission system. Old CRM lead assignees and past
Take Over names are not interpreted as current Inbox ownership.

Assignment is independent of AI/Human mode and Global AI. It sends no customer
messages, never changes learning consent and never resets unread counts, message
history, orders or workflows. New customer conversations start Unassigned;
incoming messages on an existing chat preserve its owner. WhatsApp delivery
webhooks still run normally regardless of owner or Global AI state.

An assignment version and transactional compare-and-swap reject stale admin saves.
Every meaningful change records previous/new owner, actor and UTC time in
conversation_assignment_audits. Admins can read the most recent 50 events at
`/crm/whatsapp/api/conversations/{id}/assignment-history`. No-op saves do not create
duplicate events. The UI preserves a dirty selection during polling and refreshes
after a conflict. It cannot submit a previous chat's owner while another is loading.

Reassignment clears the previous staff member's open chat on its next detail poll
(normally within four seconds), closes its dialogs, clears media/composer and
refreshes the list. New requests are denied immediately after commit. Outbound
text, product, attachment, collection and voice paths recheck ownership before
external operations. A request already crossing the Meta send boundary cannot be
recalled; permission change and Meta HTTP submission are not one transaction.
There is no automatic retry or resending of earlier customer messages.

## Railway deployment

1. Back up PostgreSQL and deploy the reviewed commit. Existing startup migration
   applies **20261103_23** after staff access revision 20261103_22. No new package
   or setting is required. Confirm `python -m alembic current` and running commit
   against Railway; a Git push alone does not establish deployment.
2. Drain all older web processes and confirm every instance has this enforcement
   before staff use the system. Old versions do not enforce ownership.
3. **Existing conversations start Unassigned.** They are not assigned from past
   takeover names or CRM lead names. Admins must assign the existing chats; staff
   will initially see an empty inbox until their chats are assigned. No chats are
   deleted. The administrator All view still shows everything.
4. Reload the Inbox to fetch versioned UI assets. Assign one owner test chat to
   Staff A, confirm A can see/reply to it, and verify Staff B cannot find it by
   search or open its bookmarked URL. Reassign to B and verify A's open view clears.
   Check unassignment, own Mine view, inactive-owner rejection and status filters.
5. Keep Global AI OFF and the working voice encoder configuration unchanged.
   Normal staff messaging still uses the existing 24-hour window/idempotency
   policies. No production assignments or test messages were performed here.

## Rollback

Prefer fixing forward while retaining row access enforcement. Do not expose a
pre-assignment version to staff: that version may reveal every Inbox chat. If
rollback is unavoidable, restrict access to admins/maintenance first, revoke
staff sessions/credentials under the account-access rollback procedure, then
restore the reviewed version. Downgrading removes ownership and assignment audit
data; export it first and do not downgrade while newer processes are active.

## Automated validation

- Ownership, admin UI, open-chat revocation, audit rollback and SQLite/PostgreSQL
  migration/concurrency checks: **28 passed**.
- Inbox, Saved Voices, conversation details, leads and staff-access regressions:
  **100 passed**.
- Assignment, attachments, reply context and Global AI control regression batch:
  **122 passed, 2 skipped** (overlaps assignment coverage above).
- Product collections, browser voice staging and staff FFmpeg voice regressions:
  **49 passed, 1 skipped**.

Skipped checks require additional optional integration configuration. These are
local automated checks, not evidence of deployment or production delivery.
