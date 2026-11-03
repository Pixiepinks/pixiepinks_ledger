# Private Inbox notes, mentions and explicit sharing

The assigned owner remains responsible for each conversation. Administrators can
share individual chats from the **Team notes** popup in the chat header. Sharing defaults to
**read and private notes**; a separate **Allow customer replies** checkbox grants
messaging permission. Only active staff with WhatsApp Inbox access are eligible.
Owners and admins already have access and need no additional share. Staff find
shared chats under **Shared with me**; Mine continues to mean their own chats.
Staff cannot use All/Unassigned or share access themselves.

Read-and-comment access covers conversation history/media/details and private
notes. It does not allow customer sends, lead changes, AI/Human changes or
ownership changes. Reading a shared chat does not clear its owner's unread count.
A reply grant permits the existing text/product/collection/media/voice send paths,
subject to existing section permissions, the 24-hour service window and send
idempotency rules. Other business modules retain their existing section access
policy; sharing does not grant access to finance, lead management or Saved Voices.

Admins can update reply permission or revoke a share. Changes use the existing
conversation permission version to reject stale updates. Grants, permission
changes and revocations are recorded transactionally in sharing audit history.
Reassignment does not silently remove explicit shares; an admin must revoke them.
A staff account's deactivation or Inbox section removal still blocks login/access.

Private notes are stored separately from WhatsApp message history and are never
sent to Meta, used as customer-visible messages or included in AI conversation
history. Notes are immutable, author/time stamped and limited to 4000 characters.
Up to 10 staff with current conversation access can be selected for a mention.
Typing @name as plain text does not create a notification or grant access.
Selecting a mention creates an internal Inbox notification only; no email,
WhatsApp message or external staff notification is sent. Notification links mark
the mention read and open the conversation. Revoked users cannot fetch notes or
notifications for that chat. The UI normally clears revoked open chats on the
next four-second history poll, and notifications refresh every 15 seconds.

Notes, mentions and sharing do not update lead stages, customer unread state,
message timestamps or automatic reply queue timers. A private note does not
answer a waiting customer. The Team notes button opens a responsive dialog with a Close button and Escape support.
The note composer is visibly separate from customer
Send, uses plain text rendering and has a retry key to avoid duplicate notes.
The latest 50 notes are shown; Load older notes pages through earlier notes.
The admin sharing panel displays the latest 30 permission audit entries.

## Deployment

1. Back up the database and deploy the reviewed commit through the normal
   migration/startup process. Confirm Alembic revision **20261103_24**. The new
   migration creates shares, sharing audit, private notes and mentions tables;
   it does not change any existing owners, messages, lead stages or flags.
2. Drain all older application processes before staff use sharing. Old processes
   do not understand shared access and may apply inconsistent permissions.
3. Reload Inbox to fetch versioned assets. As admin, share one test conversation
   with Staff B without Allow customer replies. Confirm B sees it under Shared
   with me, can read/add a note, and cannot send. Mention its assigned owner and
   check the owner's internal notification. Then grant replies and verify the
   existing send controls become available. Only send a live test if authorized.
4. Revoke access while B has the chat open. Confirm it disappears and direct
   conversation/media/notes requests fail. Check the sharing audit. Do not change
   Global AI or production voice settings for this deployment.

## Rollback

Prefer fixing forward. Restore the prior ownership-enabled application version
and reload assets if necessary. Existing owners and the prior assigned-only
security policy remain in effect; shared users lose shared visibility. The new
tables can remain safely for a forward redeploy. If a database downgrade to
20261103_23 is required, export notes, mentions, shares and sharing audit first,
drain new processes and take a backup: downgrade deletes these new tables. Do not
roll back to a version predating conversation ownership while staff are active.

A permission change cannot recall a request already submitted to Meta. Send paths
recheck access at the existing upload/submission boundaries. Revocation is enforced
on subsequent requests; polling is not a remote wipe of previously viewed data.

## Automated checks

- 10 collaboration tests passed: read-only and reply grants, CSRF, stale updates,
  audit rollback, note retry/concurrency, mention isolation, pagination, migration
  upgrade/downgrade, revoke-before-send and Chromium notes/sharing/revocation UI.
- 31 existing ownership and reply-queue checks passed.
- 144 Inbox, lead, voice, attachment, collection, migration and account-access
  regressions passed; 5 optional integration checks were skipped.

No production assignment, sharing change, WhatsApp send, AI/encoder setting change,
merge or deployment was performed during implementation.
