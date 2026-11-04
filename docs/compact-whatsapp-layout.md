# Compact WhatsApp header and scrolling chat list

Only /crm/whatsapp uses a 44px header with the small title **WhatsApp**. The
Workspace subtitle and former Conversations (SL)/shown-count row are removed.
The existing AI dot, navigation, search, filters, ownership and sharing controls
remain. Message timestamps continue to use Sri Lanka time.

Conversations load as the user scrolls, with a Load more chats fallback for
keyboard access and retries. The list is no longer limited to the first 50 chats.
The API keeps bounded pages (default 50, maximum 100 per request), adds a
nonnegative offset, and returns has_more/next_offset. Ownership, sharing, search
and queue filters are applied before pagination. Background polling refreshes the
loaded range and preserves scrolling rather than resetting to the first page.
Changing filters/search resets pagination. Live message arrivals can move chats
between pages; the next refresh reconciles the loaded range and duplicate IDs are
removed in the browser.

Deploy normally and reload assets. No migration, configuration or AI/voice setting
change is needed. Rollback restores the preceding application version; no data
rollback is required. Global AI remains OFF.

Checks cover more than 100 conversations, staff isolation, scrolling past 50,
loaded-range refresh, compact header, AI status dot and 320/360/390/430px mobile
layouts. Existing assignment, sharing, note and revocation tests are also run.
