# Single-row Inbox filters

The Inbox search input is followed by one horizontally scrolling filter row:
All, Mine, Shared with me, Unassigned (admin only), Needs our reply, Waiting for
customer, Unread, AI, Human, Payment Required and Filters. Mobile supports native
finger scrolling; desktop supports mouse-wheel scrolling and left/right arrows.
Focused buttons scroll into view. The filter row never wraps or widens the page.

Ownership options, reply-queue options and AI/Human options are each mutually
exclusive. Unread and Payment Required combine with those choices. Clicking an
active optional chip clears it. All resets every filter while preserving search.
For admins it means all conversations. For staff it means the union of owned and
explicitly shared conversations; it never exposes unassigned or other staff chats.

Filters opens a native modal dialog. Admins can select an active, Inbox-enabled
assigned owner from the existing eligible-account endpoint. Every user can filter
by the canonical lead statuses or Unorganized / no linked lead. Selecting these
filters does not change stored lead stages or assignments. Applied staff/status
filters appear as removable chips beside Filters in the same row.

Popup edits are drafts: Apply updates the list once. Clear filters prepares a full
reset that takes effect on Apply. Close or Escape discards unapplied edits and
returns focus to Filters. Removing an applied chip clears only that criterion.

## API and compatibility

The existing conversation list endpoint now additionally accepts `unread`, `mode`
(AI/HUMAN), `payment_required`, admin-only `owner_id`, and `lead_status` (canonical
value or UNORGANIZED). These predicates apply after authorization and before queue
sorting and pagination. Existing `filter=unread|ai|human|payment` URLs remain usable.
The UI records applied filters in the URL without navigating or resetting search.

The new `mailbox=accessible` returns only the current user's authorized union;
legacy staff requests for `mailbox=all` or `mailbox=unassigned` remain forbidden.
The existing default list API behavior remains unchanged when mailbox is omitted.
Filtered lists still paginate beyond 50 and refresh their loaded range. Changing
filters resets pagination and stale responses cannot replace newer filtered data.

## Deploy and rollback

Deploy the reviewed commit normally and reload Inbox to fetch versioned assets.
No migration, package, environment variable or production setting change is needed.
Verify one admin and one staff account, mobile horizontal scrolling, popup draft
cancellation, combined filters, All reset and paging under active filters. Keep
Global AI OFF and the production voice configuration unchanged. Do not send live
messages as part of these filter checks.

Rollback restores the preceding application version and reloads assets. No data
rollback is required: filtering does not mutate customer, owner or lead data.

## Validation

99 automated checks passed; 2 optional integration checks were skipped. This
includes six new filter API/browser tests, seven existing reply-queue/pagination
checks and 86 existing Inbox, lead-status, ownership, sharing and AI-control checks.
Browser coverage includes 320/360/390/430px layouts, desktop arrows, touch buttons,
canonical statuses and real staff, draft cancellation, single-apply requests,
combined filters, active-chip removal, search-preserving All/Clear, filtered
scrolling past 50, page reset, delayed-response rejection and staff access scope.
