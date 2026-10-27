# Published language in live generated replies

Previously the simulator supplied Global Instructions to the AI system prompt,
while the live generator retained its default to follow the customer's language.
Publishing a Sinhala preference therefore did not affect that live path.

Live `generate_customer_reply` now reads only `BotConfiguration.published_snapshot`
and applies the same bounded response preferences as a LIVE simulator preview.
It never initializes configuration, commits data, reads a draft or enables learning.
Published changes are read afresh on the next generated reply. Missing, invalid or
unavailable configuration preserves the existing default style and logs no content.
Security and verified Shopify/payment/order guardrails retain priority.

Choose **Current LIVE** when comparing against the inbox. DRAFT is intentionally a
preview of unpublished settings. Publish an instruction such as “Always reply in
Sinhala script, even when the customer writes in English” for Sinhala letters, or
“Always reply in Singlish using Latin letters” for transliteration. These are distinct
preferences; use the one intended by the business. Conversation history, model
variation and different product context can still produce different wording.

This fix covers generated AI replies. Fixed workflow messages, product captions,
handover notices and provider-error fallbacks retain existing wording. It does not
send messages during deployment, alter old replies, translate staff messages, change
stock filtering or implement new product recommendation workflows.

Regression tests mock providers and verify matching LIVE prompt preferences,
unpublished draft isolation, immediate published updates, request-local preview
isolation and configuration failure fallback. No real customers or AI provider calls
are needed for these tests.
