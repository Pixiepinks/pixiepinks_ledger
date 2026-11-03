# Click quoted WhatsApp messages to locate their originals

An available reply preview is now a native keyboard-accessible button. Clicking
its text or thumbnail (or pressing Enter/Space) scrolls to the exact stored
original message, focuses it, and highlights its bubble for three seconds.
Missing/self/ambiguous/cross-conversation references retain their existing
unavailable display and are not made clickable.

Photo/sticker quotes keep authenticated attachment thumbnails. Product quotes
gain a thumbnail when their stored image is HTTPS on cdn.shopify.com; unsupported
or missing image URLs remain text-only. A broken image hides gracefully and the
reply remains clickable. Quote text is rendered with textContent, not HTML.

If the original is older than the loaded latest-message list, the existing
authenticated conversation endpoint accepts around_message_id. It verifies the
target belongs to the active conversation, then returns up to 81 nearby messages
ordered chronologically. It does not use a global message lookup or expose another
customer's messages. No stored messages are edited or deleted by navigation.

The client merges that context with recent messages, retains it during ordinary
polling and bounds cached extra history to 800 messages per active conversation.
These are loaded excerpts, not a claim that every intervening historical message
is loaded. Switching conversations clears the cache and fences stale lookups.
Multiple rapid clicks also fence old responses. Normal polling preserves the
scroll anchor, message/media nodes and new-message updates.

Deployment requires only the reviewed code/static assets; no database migration,
new dependency or setting change. JS and CSS version strings are bumped. Reload
the Inbox after deployment. Verify a customer quote of an old text message, photo
and product card, including one older than the initial 100-message list. Confirm
thumbnail click and keyboard activation jump to the correct message, and repeat
on a narrow screen. Unavailable quotes should remain readable without navigation.

Global AI, learning, manual messaging, voice processing, Saved Voices, products,
orders and accounting behaviors are unchanged. Automated tests use local test
conversations and mocked media, with no messages sent to customers or Meta.
Rollback by reverting this code/static asset change; no data rollback is needed.

Validation: 111 passed, 2 skipped across reply context, Inbox, WhatsApp and Global
AI controls. Chromium covers thumbnail loading, text escaping, keyboard/mobile
jumps, loading a product outside the latest 230 messages, retention across polling
and existing quote behavior. API tests cover bounded context, missing/foreign
targets and unsafe product image URLs. The skips were not counted as passing.
