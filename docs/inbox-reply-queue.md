# Automatic Inbox reply queues

Reply queues track whose turn it is, independently of the existing manual lead
status, unread state, ownership and AI/Human mode. No lead statuses are changed.
Use All replies, Needs our reply, or Waiting for customer alongside existing
ownership, search and conversation filters. Staff still see only their assigned
conversations. Needs our reply sorts shortest waiting first, before the result limit.

A customer message starts the timer. Further customer messages preserve that
original time. A successful outbound text, voice, image, product or attachment
answers the pending messages. Successful automated replies count too. Only stored
sent/delivered/read responses count; older automatic replies with a retained Meta
message ID and no status also count. Failed or unknown sends do not count.
An asynchronous delivery failure therefore restores the earliest unanswered wait.
Opening, reading, assigning, changing a lead stage or creating an internal note
never answers a customer. Chats with neither customer messages nor a successful reply
have no reply queue yet.

Badges show elapsed wall-clock time: normal under 15 minutes, amber from 15
minutes, red from one hour. They refresh with Inbox polling (normally seven
seconds); these are visual priorities, not business-hours SLA promises. Hover a
badge for its start time in Sri Lanka time. History timestamp plus local message
ID determines ordering when timestamps are identical. Historical waits are based
on retained CRM history, not unavailable WhatsApp history.

## Deploy and rollback

Deploy the reviewed branch normally, then reload Inbox assets. No new schema,
backfill, setting, scheduled job or dependency is required. Conversation ownership
migration 20261103_23 from the preceding feature must already be applied normally.
No encoder changes or Global AI changes are required. Verify an assigned test chat:
customer question enters Needs our reply, reading does not clear it, a successful
reply enters Waiting for customer, and a new question starts a new timer. Confirm
staff cannot see another staff member's conversations in any reply filter.
Rollback to the prior ownership-enabled version and reload assets; no database
rollback is needed. Do not roll back past ownership enforcement for active staff.

## Validation

55 automated tests passed across the new reply queues, conversation ownership,
Inbox and manual lead-status editor. Coverage includes Chromium UI filters and
badges, newest-first limiting, staff isolation, multiple incoming messages,
legacy automatic replies, failed delivery recovery and unchanged lead stages.
JavaScript syntax and git whitespace checks also passed. No live messages sent.
