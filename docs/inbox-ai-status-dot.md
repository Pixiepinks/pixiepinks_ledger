# WhatsApp Inbox global AI status dot

The Inbox header uses an 8px dot instead of the former AI status banner. Red
means global automatic replies are OFF, green means ON, and gray means the status
is unavailable. The 24px button is status-only: tap to read the description, hover
for its tooltip, or use a screen reader. Escape or clicking elsewhere closes the
description. It cannot enable, disable or navigate to AI settings.

The initial state comes from the existing server-rendered Global AI display
state. The existing read-only status request still refreshes every 10 seconds
and on returning to the page. Errors or malformed/unavailable responses show gray.
Individual conversation AI/Human mode does not affect this indicator. Other pages
retain their existing AI banners and settings links.

Deploy the reviewed frontend change normally and reload Inbox assets. No migration,
new environment variable or AI toggle is required. Global AI must remain OFF.
Rollback restores the previous frontend commit and reloads assets; no data rollback
is needed. No messaging, voice, webhook or AI enable/disable code was changed.

Browser coverage includes 1366px desktop and 320/360/390/430px mobile layouts,
8px dot size, header heights, no overlap/overflow, accessible labels, descriptions,
red OFF, mocked green ON and gray on status-request failure. The new UI test
asserts that no POST or customer send occurs and its test database remains OFF.
